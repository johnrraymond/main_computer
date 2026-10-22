#!/usr/bin/env python3
"""Read-only Hawk–Dove numerical fault localization on frozen checkpoints.

No model updates. Ridge probes are fitted only on the teacher TRAIN split and
scored only on DEV. Counterfactual pairs are separately certified existing DEV
presentations with the same tier and mutation mask and different instances.
The exact X/Y oracle is used for diagnostic labels, never inference.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path

import torch
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as internal
from tools import nanojev_hawk_dove_internal_curriculum_master as master
from tools import nanojev_hawk_dove_gaussian_correction_ab as iohelp

SCHEMA='nanojev.hawk-dove.numerical-fault-localize.v1'
TIERS=tuple(fork.hd.TIERS)
DEFAULT_CHECKPOINT=internal.DEFAULT_OUTPUT/'best_checkpoints/best_macro_continuous.pt'
DEFAULT_OUTPUT=Path.home()/'NanoJev/runs/hawk_dove_numerical_fault_localize_v1'
STAGES=('numeric_atoms','visible_y_hint','frozen_parent','after_numeric_adapter',
        'after_xyop_mask','decoder_body_input','decoder_body_output',
        'bridge_body_input','bridge_body_output')


def sha(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def check_source(path:Path):
    stat=path.stat()
    raw=path.read_bytes()
    end=path.stat()
    if (stat.st_size,stat.st_mtime_ns)!=(end.st_size,end.st_mtime_ns):
        raise ValueError('FAULT_INPUT_CHANGED_DURING_READ:'+str(path))
    return raw


def load_state(data:bytes):
    return torch.load(io.BytesIO(data),map_location='cpu',weights_only=True)


def parent_bundle(path:Path,source:Path,teacher:Path,arm:str):
    """Accept immutable internal A/B or master best checkpoint and its donor."""
    path=path.expanduser().resolve()
    source=source.expanduser().resolve();teacher=teacher.expanduser().resolve()
    parent_raw=check_source(path); parent=load_state(parent_raw)
    if parent.get('schema')==internal.SCHEMA:
        if arm not in ('continuous','bank'):
            raise ValueError('FAULT_INTERNAL_ARM_REQUIRED')
        head_key=arm; ident=parent['identity']
    elif parent.get('schema')==master.SCHEMA:
        if arm not in master.ARMS:raise ValueError('FAULT_MASTER_ARM_REQUIRED')
        head_key=arm;ident=parent['identity']['donor_identity']
    else:raise ValueError('FAULT_UNSUPPORTED_CHECKPOINT_SCHEMA')
    root=path.parent.parent if path.parent.name=='best_checkpoints' else path.parent
    donor_path=root/'donor_snapshot.pt'
    donor_raw=check_source(donor_path);donor=load_state(donor_raw)
    if donor.get('schema')!=fork.SCHEMA:
        raise ValueError('FAULT_WRONG_DONOR_SCHEMA')
    if (sha(donor_raw)!=ident['donor_snapshot_sha256'] or
        int(donor['fork_step'])!=int(ident['origin_fork_step']) or
        fork.fingerprint(source)!=ident['source_sha256'] or
        fork.fingerprint(teacher)!=ident['teacher_sha256']):
        raise ValueError('FAULT_DONOR_OR_DATA_PROVENANCE_MISMATCH')
    if head_key not in parent['heads']:
        raise ValueError('FAULT_HEAD_MISSING:'+head_key)
    meta_path=path.with_suffix('.json')
    if meta_path.exists():
        meta=json.loads(check_source(meta_path))
        if (meta.get('checkpoint_sha256')!=sha(parent_raw) or
            int(meta.get('step',-1))!=int(parent['step'])):
            raise ValueError('FAULT_CHECKPOINT_METADATA_MISMATCH')
    return parent,donor,head_key,{'checkpoint_path':str(path),
        'checkpoint_sha256':sha(parent_raw),'checkpoint_step':int(parent['step']),
        'head':head_key,'donor_sha256':sha(donor_raw),
        'source_sha256':ident['source_sha256'],'teacher_sha256':ident['teacher_sha256']}


def sample_rows(teacher,split,n,seed,tiers):
    return {t:fork.science.rows_for_probe(teacher,split,t,n,seed) for t in tiers}


def capture(model,cache,decoder,head,rows,device,batch_size=4):
    """Trace learned features from the actual frozen decoder path.

    Prebody's initial channels are the model's h after BOTH additive adapters.
    Recover the pre-adapter states by subtracting the *actual* adapter outputs;
    this reconstruction is a numerical decomposition, not a second inference.
    """
    traces={s:[] for s in STAGES}
    full_prebody=[]
    truth_x=[];truth_y=[];pred_x=[];pred_y=[];base_x=[];case_ids=[]
    collector=internal.FrozenPreBody(decoder)
    try:
        for i in range(0,len(rows),batch_size):
            batch=rows[i:i+batch_size]
            prompts=[r['prompt'] for r in batch]
            if any(not fork.rt.token_eligible(model,p) for p in prompts):
                raise ValueError('FAULT_INELIGIBLE_PROMPT')
            channels=[fork.xyop.pack(r['x'],r['y'],'011') for r in batch]
            with torch.no_grad():
                evidence=collector.extract(model,prompts,channels,cache)
                pre=evidence['features']
                full_prebody.append(pre.detach().float().cpu())
                atoms=torch.as_tensor([fork.adapter.encode(p) for p in prompts],
                                      dtype=torch.float32,device=device)
                hint=torch.as_tensor(channels,dtype=torch.float32,device=device)
                numeric=decoder.numeric_adapter(atoms).float()
                mask=decoder.xyop_mask_adapter(hint).float()
                width=int(numeric.shape[-1])
                after_mask=pre[:,0,:width]
                after_numeric=after_mask-mask
                parent=after_numeric-numeric
                changed,latent=head(pre)
                # Use the original exactly-captured X as reference; the
                # downstream differential is added by the existing A/B path.
                data={**evidence,'target_x':torch.tensor([float(r['x']) for r in batch],device=device),
                      'target_y':torch.tensor([float(r['y']) for r in batch],device=device)}
                px,py,out,_=internal.predict(head,decoder,data)
                views={'numeric_atoms':atoms,'visible_y_hint':hint[:,2:4],
                       'frozen_parent':parent,'after_numeric_adapter':after_numeric,
                       'after_xyop_mask':after_mask,'decoder_body_input':pre[:,0],
                       'decoder_body_output':decoder.body(pre)[:,0],
                       'bridge_body_input':changed[:,0],
                       'bridge_body_output':decoder.body(changed)[:,0]}
                for stage,v in views.items():
                    if v.ndim!=2 or not bool(torch.isfinite(v).all()):
                        raise ValueError('FAULT_NONFINITE_STAGE:'+stage)
                    traces[stage].append(v.detach().float().cpu())
                truth_x.extend(float(r['x']) for r in batch)
                truth_y.extend(float(r['y']) for r in batch)
                pred_x.extend(px.detach().cpu().tolist())
                pred_y.extend(py.detach().cpu().tolist())
                base_x.extend(evidence['original_x'].detach().cpu().tolist())
                case_ids.extend(str(r['case_id']) for r in batch)
    finally:collector.close()
    return {'stages':{s:torch.cat(v,dim=0) for s,v in traces.items()},
            'full_prebody':torch.cat(full_prebody,dim=0),
            'x':torch.tensor(truth_x,dtype=torch.float64),
            'y':torch.tensor(truth_y,dtype=torch.float64),
            'pred_x':torch.tensor(pred_x,dtype=torch.float64),
            'pred_y':torch.tensor(pred_y,dtype=torch.float64),
            'base_x':torch.tensor(base_x,dtype=torch.float64),'case_ids':case_ids}


def join(groups,tiers):
    keys=('x','y','pred_x','pred_y','base_x')
    return {'stages':{k:torch.cat([groups[t]['stages'][k] for t in tiers]) for k in STAGES},
            **{k:torch.cat([groups[t][k] for t in tiers]) for k in keys},
            'case_ids':sum((groups[t]['case_ids'] for t in tiers),[]),
            'full_prebody':torch.cat([groups[t]['full_prebody'] for t in tiers],dim=0)}


def ridge_fit(features:torch.Tensor,target:torch.Tensor,alpha:float):
    """Deterministic standardized ridge regression with unpenalized intercept."""
    if not (math.isfinite(alpha) and alpha>0):raise ValueError('FAULT_BAD_RIDGE_ALPHA')
    x=features.to(torch.float64).cpu();y=target.to(torch.float64).cpu()/1_000_000.
    if x.ndim!=2 or y.ndim!=1 or x.shape[0]!=len(y) or len(y)<4:
        raise ValueError('FAULT_BAD_RIDGE_INPUT')
    mean=x.mean(0);std=x.std(0,unbiased=False).clamp_min(1e-5)
    z=(x-mean)/std
    offset=y.mean(); centered=y-offset
    # Dual formulation is faster when feature width exceeds number of rows.
    if x.shape[1]>x.shape[0]:
        gram=z@z.T;reg=torch.eye(len(z),dtype=torch.float64)*alpha
        dual=torch.linalg.solve(gram+reg,centered)
        coef=z.T@dual
    else:
        gram=z.T@z;reg=torch.eye(z.shape[1],dtype=torch.float64)*alpha
        coef=torch.linalg.solve(gram+reg,z.T@centered)
    return {'mean':mean,'std':std,'coef':coef,'offset':offset}


def ridge_predict(probe,features):
    x=features.to(torch.float64).cpu()
    return (((x-probe['mean'])/probe['std'])@probe['coef']+probe['offset'])*1_000_000.


def metrics(pred,true,small_cutoff=250_000.):
    true=true.to(torch.float64).cpu();pred=pred.to(torch.float64).cpu()
    if len(true)==0 or not bool(torch.isfinite(pred).all()):
        raise ValueError('FAULT_INVALID_PREDICTIONS')
    error=(pred-true).abs();relative=error/true.abs().clamp_min(1.)
    correct=relative<=.05
    def sub(mask):
        return {'n':int(mask.sum()),'correct':int(correct[mask].sum()),
                'accuracy_5pct':float(correct[mask].double().mean()) if bool(mask.any()) else None}
    return {'n':len(true),'correct':int(correct.sum()),'accuracy_5pct':float(correct.double().mean()),
            'mae':float(error.mean()),'median_absolute_error':float(error.median()),
            'median_relative_error':float(relative.median()),
            'mean_signed_error':float((pred-true).mean()),
            'sign_accuracy':float((torch.sign(pred)==torch.sign(true)).double().mean()),
            'small':sub(true.abs()<small_cutoff),'large':sub(true.abs()>=1_000_000.)}


def ridge_diagnostics(train,dev,alpha,dev_by_tier=None):
    output={}
    for stage in STAGES:
        fitx=ridge_fit(train['stages'][stage],train['x'],alpha)
        fity=ridge_fit(train['stages'][stage],train['y'],alpha)
        output[stage]={'x':metrics(ridge_predict(fitx,dev['stages'][stage]),dev['x']),
                       'y':metrics(ridge_predict(fity,dev['stages'][stage]),dev['y']),
                       'train_x':metrics(ridge_predict(fitx,train['stages'][stage]),train['x']),
                       'per_tier_x':{tier:metrics(ridge_predict(fitx,part['stages'][stage]),part['x'])
                                     for tier,part in (dev_by_tier or {}).items()},
                       'per_tier_y':{tier:metrics(ridge_predict(fity,part['stages'][stage]),part['y'])
                                     for tier,part in (dev_by_tier or {}).items()}}
    # Compare a tiny fit from the *exact visible* Y to X. Its coefficients are
    # learned, not inserted in model inference. True targets appear only in fit.
    linear=ridge_fit(train['y'][:,None],train['x'],1e-10)
    output['exact_visible_y_affine_control']={
        'x':metrics(ridge_predict(linear,dev['y'][:,None]),dev['x']),
        'coefficient_y':float(linear['coef'][0]/linear['std'][0]*1_000_000.),
        'intercept':float((linear['offset']-linear['mean'][0]/linear['std'][0]*linear['coef'][0])*1_000_000.)}
    return output


def eligible_pairs(teacher,dev_rows,tiers):
    """Pair actual dev presentations: same grammar and mask, different instance.

    Every pair comes directly from the read-only teacher database; no Y-only
    perturbations and no fabricated mathematical examples are permitted.
    """
    pairs={}
    for tier in tiers:
        entire=fork.hd.read_rows(teacher,'dev',tier)
        bymask={}
        for row in entire:bymask.setdefault(int(row['mask']),[]).append(row)
        chosen=[]
        for row in dev_rows[tier]:
            options=[r for r in bymask.get(int(row['mask']),[]) if r['instance']!=row['instance']
                     and float(r['y'])!=float(row['y'])]
            if not options:continue
            # Deterministic nearest nonidentical numeric input in same grammar.
            picked=min(options,key=lambda r:(abs(float(r['y'])-float(row['y'])),str(r['case_id'])))
            if picked['tier']!=row['tier'] or picked['mask']!=row['mask'] or picked['instance']==row['instance']:
                raise ValueError('FAULT_COUNTERFACTUAL_MISMATCH')
            chosen.append((row,picked))
        pairs[tier]=chosen
    return pairs


def counterfactual_metrics(pairs,original,alternative):
    if len(pairs)!=len(original['pred_x']) or len(pairs)!=len(alternative['pred_x']):
        raise ValueError('FAULT_COUNTERFACTUAL_CASE_COUNT_MISMATCH')
    dx=alternative['x']-original['x']
    dy=alternative['y']-original['y']
    dpx=alternative['pred_x']-original['pred_x']
    if bool((dy==0).any()):raise ValueError('FAULT_ZERO_DELTA_Y')
    # Fit oracle slope on actual changes. This is a DIAGNOSTIC relationship,
    # never injected into the model or its predictions.
    slope=(dx*dy).sum()/(dy*dy).sum()
    raw_slope=dpx/dy
    abs_dx=dx.abs().clamp_min(1.)
    delta_error=(dpx-dx).abs()/abs_dx
    return {'pairs':len(dx),'oracle_slope_dX_dY':float(slope),
            'median_model_slope_dX_dY':float(raw_slope.median()),
            'mean_model_slope_dX_dY':float(raw_slope.mean()),
            'median_relative_delta_x_error':float(delta_error.median()),
            'correct_delta_x_within_5pct':int((delta_error<=.05).sum()),
            'delta_x_5pct':float((delta_error<=.05).double().mean()),
            'median_abs_delta_x':float(dx.abs().median()),
            'median_abs_predicted_delta_x':float(dpx.abs().median()),
            'note':'Valid co-varying game-count changes, not a Y-only causal perturbation'}


def cosine_stats(grads):
    values={}
    names=list(grads)
    for i,a in enumerate(names):
        for b in names[i+1:]:
            ga=grads[a].reshape(-1);gb=grads[b].reshape(-1)
            va=float(ga.norm());vb=float(gb.norm())
            if va<1e-12 or vb<1e-12:cos=None
            else:cos=float(F.cosine_similarity(ga[None],gb[None]).item())
            xa=grads[a].reshape(len(grads[a]),-1)
            xb=grads[b].reshape(len(grads[b]),-1)
            na=xa.norm(dim=1);nb=xb.norm(dim=1)
            valid=(na>1e-12)&(nb>1e-12)
            local=((xa*xb).sum(dim=1)/(na*nb).clamp_min(1e-24))[valid]
            values[a+'_vs_'+b]={
                'cosine':cos,'opposing':bool(cos<0) if cos is not None else None,
                'norm_a':va,'norm_b':vb,'valid_cases':int(valid.sum()),
                'mean_per_case_cosine':float(local.mean()) if len(local) else None,
                'opposing_case_fraction':float((local<0).float().mean()) if len(local) else None}
    return values


def gradient_audit(decoder,head,trace,max_examples=16):
    """Diagnostic gradients with respect to frozen pre-body ACTIVATIONS.

    Gaussian pressure is reported as a hypothetical gradient even if the
    candidate training configuration used gaussian_weight=0. It is not the
    actual trained optimizer gradient, and no parameters are updated.
    """
    n=min(int(max_examples),len(trace['x']))
    if n<1:return {'cases':0}
    pre=trace['full_prebody'][:n].to(next(decoder.parameters()).device).detach().requires_grad_(True)
    x=trace['x'][:n].to(pre.device,dtype=torch.float32)
    y=trace['y'][:n].to(pre.device,dtype=torch.float32)
    changed,_=head(pre)
    out=internal.decode_frozen(decoder,changed)
    gx=torch.log10(1+x.abs())
    gy=torch.log10(1+y.abs())
    losses={'x_magnitude':F.smooth_l1_loss(out['log_magnitude'][:,0],gx,beta=.05),
            'x_sign':F.cross_entropy(out['sign'][:,0],torch.where(x<0,0,torch.where(x>0,2,1)).long()),
            'y_magnitude':F.smooth_l1_loss(out['log_magnitude'][:,1],gy,beta=.05)}
    sigma=float(decoder.sigma)
    q=F.softmax(-.5*((decoder.centers[None,:]-gx[:,None])/sigma).square(),dim=-1)
    losses['gaussian_x']=F.kl_div(F.log_softmax(out['bank_logits'][:,0],dim=-1),q,
                                  reduction='batchmean')
    grads={name:torch.autograd.grad(loss,pre,retain_graph=True,allow_unused=False)[0].detach().cpu()
           for name,loss in losses.items()}
    return {'cases':n,'gradient_space':'frozen_decoder_prebody_activation',
            'loss_values':{k:float(v.detach()) for k,v in losses.items()},
            'gradient_norms':{k:float(g.norm()) for k,g in grads.items()},
            'pairwise':cosine_stats(grads),
            'gaussian_x_is_hypothetical':True}


def verify_no_overlap(output,inputs):
    out=output.expanduser().resolve()
    for item in inputs:
        p=item.expanduser().resolve()
        if out==p or out in p.parents or p in out.parents:
            raise ValueError('FAULT_OUTPUT_OVERLAPS_PROTECTED_INPUT:'+str(p))
    return out


def run(args):
    tiers=tuple(TIERS if args.tiers=='all' else args.tiers.split(','))
    if (not tiers or len(set(tiers))!=len(tiers) or any(t not in TIERS for t in tiers)
        or args.train_per_tier<8 or args.dev_per_tier<8 or args.batch_size<1
        or args.ridge_alpha<=0 or args.grad_examples<0 or args.counterfactual_limit<0):
        raise ValueError('FAULT_BAD_ARGUMENTS')
    parent,donor,arm,ident=parent_bundle(args.checkpoint,args.source,args.teacher,args.arm)
    out=verify_no_overlap(args.output,[args.checkpoint.parent,args.source,args.teacher,
                                         args.checkpoint.parent.parent])
    if out.exists() and any(out.iterdir()):
        raise FileExistsError('FAULT_OUTPUT_ALREADY_EXISTS:'+str(out))
    # The output directory is created only after all source identity checks.
    fork.emit('fault_prepare',checkpoint_step=ident['checkpoint_step'],arm=arm,
              checkpoint_sha256=ident['checkpoint_sha256'],
              train_rows_per_tier=args.train_per_tier,dev_rows_per_tier=args.dev_per_tier,
              test_split_read=False,model_training=False)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    model['decoder']=fork.build_for_resume(model,donor,
            parent['identity']['donor_identity']['parent_registers'] if parent['schema']==master.SCHEMA
            else parent['identity']['parent_registers'],
            parent['identity']['donor_identity']['parent_sigma_steps'] if parent['schema']==master.SCHEMA
            else parent['identity']['parent_sigma_steps'])
    decoder=model['decoder'];decoder.eval().requires_grad_(False)
    # Original V3 body begins with LayerNorm whose normalized_shape is the
    # complete prebody width. A linear fallback remains for test doubles.
    layer=decoder.body[0]
    bridge_width=(int(layer.normalized_shape[0]) if hasattr(layer,'normalized_shape')
                  else int(layer.in_features) if hasattr(layer,'in_features') else None)
    if bridge_width is None:raise ValueError('FAULT_CANNOT_ESTABLISH_PREBODY_WIDTH')
    kind='bank' if arm.startswith('bank') else 'continuous'
    head=internal.IntermediateBridge(bridge_width,
         parent['identity']['donor_identity']['registers'] if parent['schema']==master.SCHEMA else parent['identity']['registers'],
         kind=kind,
         limit=(parent['identity']['donor_identity'] if parent['schema']==master.SCHEMA else parent['identity'])['limit'],
         sigma_bins=(parent['identity']['donor_identity'] if parent['schema']==master.SCHEMA else parent['identity'])['sigma_bins'],
         feedback_scale=(parent['identity']['donor_identity'] if parent['schema']==master.SCHEMA else parent['identity'])['feedback_scale']).to(model['device'])
    head.load_state_dict(parent['heads'][arm],strict=True);head.eval().requires_grad_(False)
    out.mkdir(parents=True,exist_ok=True)
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',{
        'schema':SCHEMA+'.capture','source_sha256':ident['source_sha256'],
        'teacher_sha256':ident['teacher_sha256'],
        'capture_max_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    samples_train=sample_rows(args.teacher,'train',args.train_per_tier,args.seed,tiers)
    samples_dev=sample_rows(args.teacher,'dev',args.dev_per_tier,args.seed,tiers)
    train={};dev={}
    for tier in tiers:
        train[tier]=capture(model,cache,decoder,head,samples_train[tier],model['device'],args.batch_size)
        dev[tier]=capture(model,cache,decoder,head,samples_dev[tier],model['device'],internal.DEV_BATCH)
        fork.emit('fault_features_captured',tier=tier,train=len(train[tier]['x']),dev=len(dev[tier]['x']))
    train_instances={int(r['instance']) for tier in tiers for r in samples_train[tier]}
    dev_instances={int(r['instance']) for tier in tiers for r in samples_dev[tier]}
    if train_instances & dev_instances:
        raise ValueError('FAULT_TRAIN_DEV_INSTANCE_LEAKAGE')
    pooled_train=join(train,tiers);pooled_dev=join(dev,tiers)
    probes=ridge_diagnostics(pooled_train,pooled_dev,args.ridge_alpha,dev)
    final_by_tier={t:{'donor_x':metrics(dev[t]['base_x'],dev[t]['x']),
                      'candidate_x':metrics(dev[t]['pred_x'],dev[t]['x'])} for t in tiers}
    pairs=eligible_pairs(args.teacher,samples_dev,tiers)
    cf={}
    for tier in tiers:
        subset=pairs[tier][:args.counterfactual_limit]
        if not subset:cf[tier]={'pairs':0,'unavailable':'no same-mask distinct-instance dev pair'};continue
        alt=capture(model,cache,decoder,head,[b for a,b in subset],model['device'],internal.DEV_BATCH)
        chosen_indices=[samples_dev[tier].index(a) for a,b in subset]
        orig={'x':dev[tier]['x'][chosen_indices],'y':dev[tier]['y'][chosen_indices],
              'pred_x':dev[tier]['pred_x'][chosen_indices]}
        cf[tier]=counterfactual_metrics(subset,orig,alt)
        fork.emit('fault_counterfactual_tier',tier=tier,pairs=cf[tier]['pairs'],
                  model_delta_x_5pct=cf[tier]['delta_x_5pct'])
    grad={'status':'not_requested'} if args.grad_examples==0 else {
        t:gradient_audit(decoder,head,dev[t],max_examples=args.grad_examples) for t in tiers}
    report={'schema':SCHEMA,'provenance':ident,'split_policy':{
        'probe_fit':'train_only','score':'dev_only','counterfactual':'dev_only',
        'test_split_accessed':False,'model_optimized':False,
        'distinct_train_instances':len(train_instances),
        'distinct_dev_instances':len(dev_instances)},
        'tiers':list(tiers),'probe_alpha':args.ridge_alpha,
        'readout_by_tier':final_by_tier,'linear_probes':probes,
        'counterfactual_pairs':cf,'gradient_audit':grad,
        'interpretation_rules':{
            'linear_probe_success':'Numerical information is linearly decodable from that stage; not proof model uses it.',
            'linear_probe_failure':'Does not establish information is absent; it may be nonlinear.',
            'consistent_counterfactual':'Both game counts and visible Y change consistently; not intervention on Y alone.',
            'heldout_claim':'Dev split excludes train numerical instances; test split intentionally untouched.'}}
    target=out/'fault-localization.json'
    iohelp.atom_json(target,report)
    # Case-level evidence for inspecting systematic bias and repeated values.
    import io as _io
    buf=_io.StringIO()
    fields=['tier','case_id','true_x','true_y','donor_x','candidate_x',
            'candidate_y','relative_error_x','small_x']
    writer=csv.DictWriter(buf,fieldnames=fields);writer.writeheader()
    for tier in tiers:
        part=dev[tier]
        for i,case in enumerate(part['case_ids']):
            x=float(part['x'][i]);pred=float(part['pred_x'][i])
            writer.writerow(dict(tier=tier,case_id=case,true_x=x,true_y=float(part['y'][i]),
                 donor_x=float(part['base_x'][i]),candidate_x=pred,
                 candidate_y=float(part['pred_y'][i]),
                 relative_error_x=abs(pred-x)/max(1,abs(x)),small_x=abs(x)<250000))
    iohelp.atomic_bytes(out/'dev-cases.csv',buf.getvalue().encode('utf-8'))
    if fork.fingerprint(args.checkpoint)!=ident['checkpoint_sha256']:
        raise ValueError('FAULT_CHECKPOINT_CHANGED_DURING_DIAGNOSIS')
    fork.emit('fault_finished',report=str(target),candidate_macro=sum(final_by_tier[t]['candidate_x']['accuracy_5pct'] for t in tiers)/len(tiers),
              checkpoint_unchanged=True,test_split_read=False)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,default=DEFAULT_CHECKPOINT)
    p.add_argument('--arm',default='continuous',help='continuous/bank for internal A/B; continuous_control/etc for master')
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float32','float16','bfloat16'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--train-per-tier',type=int,default=128)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--ridge-alpha',type=float,default=1.)
    p.add_argument('--counterfactual-limit',type=int,default=24)
    p.add_argument('--grad-examples',type=int,default=8)
    p.add_argument('--tiers',default='all',help='Comma-separated T00..T08 or all')
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
