#!/usr/bin/env python3
"""Correct HybridLink/Qwen embedding scale, realign direction, test cache and blind utility.

Four independently checkpointed, fail-closed stages. Does not modify the
published HybridLink internal structure, Qwen weights, or source checkpoints.
Only the NEW head copy and one learned output log-gain receive gradients.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_qwen_hybrid_interface_repair as interface
from tools import nanojev_qwen_hybrid_latent_geometry as geometry
from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
from tools import nanojev_qwen_zero_line_backbone as zero

SCHEMA = 'nanojev.qwen3-06b.hybrid-alignment-repair.v1'
ROOT = Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_alignment_repair_v1'


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _require_new(target, *, allow_existing=False):
    path=Path(target)
    if not allow_existing:
        assert not path.exists() or not any(path.iterdir()), 'ALIGN_REPAIR_OUTPUT_EXISTS:'+str(path)
    path.mkdir(parents=True,exist_ok=True)


def _sources(interface_dir):
    _, hashes=geometry._preflight(interface_dir)
    return hashes


def _verify_output_root(root,base,numerical,interface_dir):
    root=Path(root).resolve()
    for source in (base,numerical,interface_dir):
        source=Path(source).resolve()
        assert root!=source and root not in source.parents and source not in root.parents, 'ALIGN_REPAIR_SOURCE_OUTPUT_OVERLAP'


def _load(*,device,base,numerical,interface_dir,loader=None,allow_download=False):
    expected=_sources(interface_dir)
    model,tok,_,cfg,_=interface._load_format(device=device,base=base,numerical=numerical,
                       output=interface_dir,loader=loader,allow_download=allow_download)
    head_cfg=_json(Path(interface_dir)/'align'/'config.json')
    cp=torch.load(Path(interface_dir)/'align'/'best.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==interface.SCHEMA and cp['config_hash']==interface._hash_config(head_cfg), 'ALIGN_REPAIR_HEAD_INVALID'
    model.head.to(device=device,dtype=torch.float32)
    model.head.load_state_dict(cp['head'],strict=True)
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(False)
    model.eval()
    assert _sources(interface_dir)==expected,'ALIGN_REPAIR_CHECKPOINT_CHANGED'
    return model,tok,cfg,expected


class ScaledHead(nn.Module):
    """External output gain: existing HybridLink module remains bit-for-bit intact."""
    def __init__(self, head, initial_scale:float):
        super().__init__()
        assert 1e-5 < initial_scale < 1.,'ALIGN_REPAIR_SCALE_UNSAFE'
        self.head=head
        self.log_gain=nn.Parameter(torch.tensor(math.log(initial_scale),dtype=torch.float32))

    def forward(self,x):
        assert torch.isfinite(self.log_gain).all(),'ALIGN_REPAIR_BAD_GAIN'
        return self.head(x)*torch.exp(self.log_gain)

    def gain(self):
        return float(self.log_gain.detach().exp())


def calibration_summary(head,dev_pairs):
    # _pairs stores teacher targets on CPU to conserve GPU RAM; the trained
    # HybridLink remains on CUDA.  Geometry must execute on the HEAD device.
    head_device=next(head.parameters()).device
    g=geometry.measure_geometry(head,dev_pairs,device=head_device)
    opts={'median_norm':g['calibration']['median_norm_scale'],
          'least_squares':g['calibration']['least_squares_scale_signed']}
    x,y,_=dev_pairs
    with torch.inference_mode():
        p=head(x.to(device=next(head.parameters()).device,dtype=next(head.parameters()).dtype)).float().cpu()
    zero_mse=float(y.square().mean())
    assert zero_mse>0 and math.isfinite(zero_mse),'ALIGN_REPAIR_ZERO_REFERENCE_INVALID'
    cand={}
    for name,value in opts.items():
        if not 1e-5 < value < 1.:continue
        v=float(F.mse_loss(p*value,y.float()))
        cand[name]={'gain':float(value),'mse':v,'mse_ratio_to_zero':v/zero_mse}
    assert cand,'ALIGN_REPAIR_NO_SAFE_CALIBRATION'
    # Choose solely using DEV representation loss, not held-out answer accuracy.
    winner=min(cand,key=lambda key:cand[key]['mse'])
    return {'selected':winner,'gain':cand[winner]['gain'],'candidates':cand,
            'dev_geometry':g,'zero_mse':zero_mse}


def _run_calibrate(model,tok,fc,interface_dir,root,hashes,device):
    data=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])
    dev_pairs=interface._pairs(model,tok,data['dev'],device,fc['max_seq'])
    cal=calibration_summary(model.head,dev_pairs)
    record={'schema':SCHEMA,'stage':'calibrate','sources':hashes,
            'format_data_fingerprint':zero.fingerprint(data),'calibration_source':'dev_only',
            'selected':cal['selected'],'gain':cal['gain'],'candidates':cal['candidates'],
            'zero_mse':cal['zero_mse'], 'dev_geometry':cal['dev_geometry'],
            'status':'CALIBRATED_UNCERTIFIED'}
    zero.atomic_json(root/'calibration.json',record)
    print('HYBRID_ALIGN_CALIBRATION='+json.dumps({k:v for k,v in record.items() if k!='dev_geometry'},sort_keys=True),flush=True)
    return record


def _require_cal(root,hashes):
    r=_json(root/'calibration.json')
    assert r['schema']==SCHEMA and r['sources']==hashes and r['status']=='CALIBRATED_UNCERTIFIED', 'ALIGN_REPAIR_CALIBRATION_DRIFT'
    assert r['calibration_source']=='dev_only' and 1e-5 < r['gain'] < 1.,'ALIGN_REPAIR_INVALID_CALIBRATION'
    return r


def _metrics(head,pairs,batch=64):
    x,y,flags=pairs
    device=next(head.parameters()).device
    was=head.training
    head.eval()
    with torch.inference_mode():
        pred=torch.cat([head(x[i:i+batch].to(device,dtype=torch.float32)).float().cpu() for i in range(0,len(x),batch)])
    head.train(was)
    assert pred.shape==y.shape and torch.isfinite(pred).all(),'ALIGN_REPAIR_BAD_PREDICTION'
    cosine=F.cosine_similarity(pred,y.float(),dim=-1,eps=1e-12)
    norm_ratio=pred.norm(dim=-1)/y.norm(dim=-1).clamp_min(1e-10)
    mse=float(F.mse_loss(pred,y.float()))
    zero_mse=float(y.float().square().mean())
    nonexit=~flags
    assert bool(nonexit.any()) and bool(flags.any()),'ALIGN_REPAIR_MISSING_CLASSES'
    return {'relative_mse_to_zero':mse/zero_mse,'mse':mse,
            'non_exit_cosine':float(cosine[nonexit].mean()),'exit_cosine':float(cosine[flags].mean()),
            'median_norm_ratio':float(norm_ratio.median()),'zero_mse':zero_mse}


def alignment_loss(head,x,y,flags):
    """Scale-sensitive regression plus cosine and semantic negative controls.

    Identical target-token embeddings are excluded from contrastive negatives,
    otherwise duplicated teacher targets would create contradictory gradients.
    """
    pred=head(x)
    assert torch.isfinite(pred).all(),'ALIGN_REPAIR_NONFINITE_HEAD'
    denom=y.square().mean().detach().clamp_min(1e-9)
    relative_mse=F.mse_loss(pred,y)/denom
    cosine=F.cosine_similarity(pred,y,dim=-1,eps=1e-9)
    focus=torch.where(flags,torch.full_like(cosine,0.25),torch.ones_like(cosine))
    direction=(focus*(1-cosine)).sum()/focus.sum()
    norm_diff=((pred.norm(dim=-1)-y.norm(dim=-1))/y.norm(dim=-1).clamp_min(1e-6)).square().mean()
    # Within-batch mismatched target vectors (exclude exact duplicate target rows).
    pn=F.normalize(pred,dim=-1,eps=1e-8)
    yn=F.normalize(y,dim=-1,eps=1e-8)
    neg=pn@yn.T
    sim_targets=yn@yn.T
    valid=(sim_targets<0.999) & ~torch.eye(len(x),dtype=torch.bool,device=x.device)
    if bool(valid.any()):
        margin=F.relu(0.1+neg-cosine[:,None])
        negative=(margin*valid).sum()/valid.sum()
    else:
        negative=pred.new_zeros(())
    loss=relative_mse+0.5*direction+0.05*norm_diff+0.1*negative
    assert bool(torch.isfinite(loss)),'ALIGN_REPAIR_INVALID_LOSS'
    return {'loss':loss,'relative_mse':relative_mse,'direction':direction,
            'norm':norm_diff,'contrastive':negative}


def _config(hashes,cal,fc,steps,lr,gain_lr,eval_every):
    return {'schema':SCHEMA,'stage':'train','sources':hashes,
            'calibration_fingerprint':zero.fingerprint(cal),
            'format_data':[fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count']],
            'steps':steps,'lr':lr,'gain_lr':gain_lr,'eval_every':eval_every,
            'architecture':'HybridLink_post_ln_unchanged_plus_output_log_gain'}


def _train(model,tok,fc,hashes,root,device,*,interface_dir,steps=240,lr=4e-5,gain_lr=1e-4,eval_every=20,resume=False,stop_after=None):
    assert 1<=steps<=10000 and 1<=eval_every<=steps and 0<lr<=1e-3 and 0<gain_lr<=1e-2,'ALIGN_REPAIR_BAD_TRAIN_ARGS'
    assert stop_after is None or 1<=stop_after<=steps,'ALIGN_REPAIR_BAD_STOP'
    cal=_require_cal(root,hashes)
    cfg=_config(hashes,cal,fc,steps,lr,gain_lr,eval_every)
    folder=root/'alignment'
    if folder.exists() and any(folder.iterdir()):
        assert (folder/'config.json').is_file() and _json(folder/'config.json')==cfg,'ALIGN_REPAIR_CONFIG_DRIFT'
        assert resume or {p.name for p in folder.iterdir()}=={'config.json'},'ALIGN_REPAIR_TRAIN_ALREADY_EXISTS'
    else:
        _require_new(folder)
        zero.atomic_json(folder/'config.json',cfg)
    net=ScaledHead(model.head,cal['gain']).to(device=device,dtype=torch.float32)
    net.head.requires_grad_(True)
    net.log_gain.requires_grad_(True)
    model.backbone.requires_grad_(False)
    data=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])
    train=interface._pairs(model,tok,data['train'],device,fc['max_seq'])
    dev=interface._pairs(model,tok,data['dev'],device,fc['max_seq'])
    prior=_metrics(net,dev)
    opt=torch.optim.AdamW([{'params':list(net.head.parameters()),'lr':lr},
                           {'params':[net.log_gain],'lr':gain_lr}],weight_decay=0.)
    cfg_hash=zero.fingerprint(cfg)
    if resume:
        last=torch.load(folder/'last.pt',map_location='cpu',weights_only=True)
        assert last['schema']==SCHEMA and last['config_hash']==cfg_hash and 0<last['step']<steps,'ALIGN_REPAIR_RESUME_INVALID'
        net.load_state_dict(last['state'],strict=True)
        opt.load_state_dict(last['optimizer'])
        torch.set_rng_state(last['rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(last['cuda_rng'])
        start=int(last['step']);best=float(last['best_objective']);prior=last['prior']
    else:
        start=0;best=float('inf')
    initial_base=zero.frozen_backbone_sha256(model.backbone)
    lora_before=zero.lora_state(model.backbone)
    n=len(train[0])
    x,y,flags=train
    last_metric={}
    for step in range(start+1,steps+1):
        indices=torch.arange((step-1)*32,(step-1)*32+32)%n
        a=x[indices].to(device);b=y[indices].to(device);c=flags[indices].to(device)
        net.train();opt.zero_grad(set_to_none=True)
        terms=alignment_loss(net,a,b,c)
        terms['loss'].backward()
        gradients=[p.grad for p in net.parameters()]
        assert all(g is not None and torch.isfinite(g).all() for g in gradients),'ALIGN_REPAIR_GRADIENT_INVALID'
        assert any(bool(g.abs().sum()>0) for g in gradients),'ALIGN_REPAIR_ZERO_GRADIENT'
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        opt.step()
        with torch.no_grad():
            assert 1e-5<net.gain()<1.,'ALIGN_REPAIR_GAIN_ESCAPED_SAFE_RANGE'
        if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
            met=_metrics(net,dev)
            # Select by geometry on dev only. NEVER select by blind test accuracy.
            objective=met['relative_mse_to_zero']+0.5*(1-met['non_exit_cosine'])
            last_metric=met
            if objective<best:
                best=objective
                zero.atomic_torch(folder/'best.pt',{'schema':SCHEMA,'config_hash':cfg_hash,
                    'step':step,'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()},'dev':met})
            zero.atomic_torch(folder/'last.pt',{'schema':SCHEMA,'config_hash':cfg_hash,'step':step,
               'state':{k:v.detach().cpu().clone() for k,v in net.state_dict().items()},
               'optimizer':opt.state_dict(),'prior':prior,'best_objective':best,
               'rng':torch.get_rng_state(),
               'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None})
            print('HYBRID_ALIGN_STEP='+json.dumps({'step':step,'total':steps,'gain':net.gain(),
                'train_loss':float(terms['loss'].detach()),'dev':met,'best_objective':best},sort_keys=True),flush=True)
        if stop_after and step>=stop_after:break
    assert zero.frozen_backbone_sha256(model.backbone)==initial_base,'ALIGN_REPAIR_BACKBONE_MUTATED'
    assert all(torch.equal(lora_before[k],zero.lora_state(model.backbone)[k]) for k in lora_before),'ALIGN_REPAIR_LORA_MUTATED'
    _sources_checked=_sources(interface_dir)
    assert _sources_checked==hashes,'ALIGN_REPAIR_SOURCE_MUTATED'
    status='TRAINED_UNCERTIFIED' if step==steps else 'PAUSED_RESUMABLE'
    out={'schema':SCHEMA,'status':status,'step':step,'steps':steps,'prior':prior,
         'last':last_metric,'best_objective':best,'backbone_mutations':0,
         'format_checkpoint_sha256':hashes['format'],'original_head_sha256':hashes['head']}
    zero.atomic_json(folder/'result.json',out)
    print('HYBRID_ALIGN_TRAIN_RESULT='+json.dumps(out,sort_keys=True),flush=True)
    return out


def _restore(model,root,hashes):
    folder=root/'alignment'
    conf=_json(folder/'config.json')
    r=_json(folder/'result.json')
    assert conf['schema']==SCHEMA and r['status']=='TRAINED_UNCERTIFIED', 'ALIGN_REPAIR_TRAIN_NOT_COMPLETE'
    assert conf['sources']==hashes and conf['calibration_fingerprint']==zero.fingerprint(_require_cal(root,hashes)),'ALIGN_REPAIR_SOURCE_OR_CAL_DRIFT'
    best=torch.load(folder/'best.pt',map_location='cpu',weights_only=True)
    assert best['schema']==SCHEMA and best['config_hash']==zero.fingerprint(conf),'ALIGN_REPAIR_BEST_CHECKPOINT_DRIFT'
    net=ScaledHead(model.head,_require_cal(root,hashes)['gain'])
    net.load_state_dict(best['state'],strict=True)
    net.eval().requires_grad_(False)
    return net.to(next(model.head.parameters()).device),best


def cache_equivalence(model,tok,items,*,device,head,tolerance=0.05):
    """Cached vs full-sequence on the SAME generated latent; no teacher input."""
    assert 0<tolerance<0.5,'ALIGN_REPAIR_BAD_CACHE_TOLERANCE'
    prior=model.head;model.head=head
    details=[]
    try:
        model.eval()
        with torch.inference_mode():
            for item in items:
                toks=tok('Question: Compute '+item['expression']+'.\nReasoning: ',add_special_tokens=False)['input_ids']
                ids=torch.tensor([toks+[model.start_id]],device=device,dtype=torch.long)
                h,_,cache=model._step(input_ids=ids,mask=torch.ones_like(ids))
                v=model.head(h.float()).to(model.backbone.get_input_embeddings().weight.dtype)
                mask=torch.ones((1,ids.shape[1]+1),device=device,dtype=torch.long)
                _,_,cache=model._step(inputs_embeds=v[:,None,:],cache=cache,mask=mask)
                mask=torch.ones((1,ids.shape[1]+2),device=device,dtype=torch.long)
                exitids=ids.new_tensor([[model.end_id]])
                cached_h,cached_logit,_=model._step(input_ids=exitids,cache=cache,mask=mask)
                embedding=model.backbone.get_input_embeddings()
                seq=torch.cat((embedding(ids),v[:,None,:],embedding(exitids)),dim=1)
                result=model.backbone(inputs_embeds=seq,attention_mask=mask,
                    output_hidden_states=True,use_cache=False,return_dict=True)
                full_h=result.hidden_states[-1][:,-1,:]
                full_logit=result.logits[:,-1,:]
                def error(a,b):
                    a=a.float();b=b.float()
                    return float((a-b).square().mean().sqrt()/(a.square().mean().sqrt().clamp_min(1e-5)))
                hr=error(full_h,cached_h)
                lr=error(full_logit,cached_logit)
                token_equal=int(full_logit.argmax(-1).item())==int(cached_logit.argmax(-1).item())
                # Marker-only control tests the boundary independent of HybridLink.
                _,_,marker_cache=model._step(input_ids=ids,mask=torch.ones_like(ids))
                marker_mask=torch.ones((1,ids.shape[1]+1),device=device,dtype=torch.long)
                mh,ml,_=model._step(input_ids=exitids,cache=marker_cache,mask=marker_mask)
                marker_full=model.backbone(input_ids=torch.cat((ids,exitids),dim=1),
                    attention_mask=marker_mask,output_hidden_states=True,use_cache=False,return_dict=True)
                marker_hr=error(marker_full.hidden_states[-1][:,-1,:],mh)
                marker_lr=error(marker_full.logits[:,-1,:],ml)
                marker_argmax=int(marker_full.logits[:,-1,:].argmax())==int(ml.argmax())
                details.append({'id':item['id'],'hidden_relative_rmse':hr,'logit_relative_rmse':lr,
                    'argmax_equal':token_equal,'marker_hidden_relative_rmse':marker_hr,
                    'marker_logit_relative_rmse':marker_lr,'marker_argmax_equal':marker_argmax,
                    'latent_input_norm':float(v.float().norm())})
    finally:
        model.head=prior
    maximum=max(max(d[k] for k in ('hidden_relative_rmse','logit_relative_rmse',
                            'marker_hidden_relative_rmse','marker_logit_relative_rmse')) for d in details)
    passed=all(max(d[k] for k in ('hidden_relative_rmse','logit_relative_rmse',
                            'marker_hidden_relative_rmse','marker_logit_relative_rmse'))<=tolerance
               and d['argmax_equal'] and d['marker_argmax_equal'] for d in details)
    return {'schema':SCHEMA,'status':'RECURRENCE_PASS' if passed else 'RECURRENCE_FAIL',
            'pass':passed,'tolerance':tolerance,'max_relative_rmse':maximum,'checks':len(details),'records':details}


def _shuffled_tokens(model,tokenizer,rows,device):
    # No gold answer is read in this generator. Shuffle the latent states by
    # replacing head(x) with a head output obtained from an OTHER prompt.
    vectors=[]
    with torch.inference_mode():
        for row in rows:
            toks=tokenizer('Question: Compute '+row['expression']+'.\nReasoning: ',add_special_tokens=False)['input_ids']
            ids=torch.tensor([toks+[model.start_id]],device=device,dtype=torch.long)
            h,_,_=model._step(input_ids=ids,mask=torch.ones_like(ids))
            vectors.append(model.head(h.float()).detach())
    assert len(vectors)>1 and all(v.shape==vectors[0].shape for v in vectors),'ALIGN_REPAIR_SHUFFLE_INVALID'
    return [vectors[(i+1)%len(vectors)] for i in range(len(vectors))]


class ConstantLatent(nn.Module):
    def __init__(self, original, state):
        super().__init__();self.original=original;self.state=state
    def forward(self,x):
        return self.state.to(device=x.device,dtype=x.dtype).expand_as(x)


def _decode(model,tok,ids,cond,budget,shuffled):
    if cond=='text':return paired.decode_once(model,tok,ids,0,budget)
    if cond=='marker_only':return geometry.decode_marker_only(model,tok,ids,budget)
    original=model.head
    if cond in ('zero','shuffled'):
        state=(torch.zeros((1,model.backbone.get_input_embeddings().weight.shape[1]),device=ids.device)
               if cond=='zero' else shuffled)
        model.head=ConstantLatent(original,state)
    try:
        return paired.decode_once(model,tok,ids,1,budget)
    finally:
        model.head=original


def behavioural_gate(model,tok,head,rows,*,device,budget=48):
    assert len(rows)>=6 and len(rows)%6==0 and budget>=1,'ALIGN_REPAIR_INVALID_GATE_SET'
    conditions=('text','marker_only','zero','shuffled','intact')
    records=[]
    original=model.head
    model.head=head
    try:
        model.eval()
        shifted=_shuffled_tokens(model,tok,rows,device)
        with torch.inference_mode():
            for i,row in enumerate(rows):
                text='Question: Compute '+row['expression']+'.\nReasoning: '
                ids=torch.tensor([tok(text,add_special_tokens=False)['input_ids']],device=device,dtype=torch.long)
                variants={}
                for cond in conditions:
                    out=_decode(model,tok,ids,cond,budget,shifted[i])
                    variants[cond]={'answer':out['answer_label'],'parseable':out['answer_label'] is not None,
                         'completion':out['completion'],'tokens':out['tokens'],'seconds':out['seconds']}
                entry={'id':row['id'],'family':row['family'],'sign':row['sign'],
                       'evaluation_group':'independent_blind' if row['id'].startswith('independent_') else 'original_regression',
                       'expected':row['answer'],'variants':variants}
                records.append(entry)
                print('HYBRID_ALIGN_BLIND_CASE='+json.dumps(entry,sort_keys=True,ensure_ascii=False),flush=True)
    finally:
        model.head=original
    score={c:{'correct':sum(r['variants'][c]['answer']==r['expected'] for r in records),
        'parseable':sum(r['variants'][c]['parseable'] for r in records),'cases':len(records)} for c in conditions}
    n=len(records)
    strong=(score['intact']['correct']>=score['text']['correct']-1 and
            score['intact']['correct']>=score['zero']['correct']+2 and
            score['intact']['correct']>=score['shuffled']['correct']+2 and
            score['intact']['parseable']>=score['text']['parseable']-1)
    by_group={g:{c:sum(r['variants'][c]['answer']==r['expected'] for r in records if r['evaluation_group']==g)
                 for c in conditions} for g in ('original_regression','independent_blind')}
    return {'score':score,'by_group':by_group,'latent_advantage_pass':bool(strong),'records':records,
            'criteria':{'max_regression_vs_text':1,'minimum_gain_vs_zero':2,
                        'minimum_gain_vs_shuffled':2,'max_parseability_regression':1},'cases':n}


def _gate_rows(fc,n):
    assert n>=6 and n%6==0 and n<=120,'ALIGN_REPAIR_INVALID_CASES'
    original=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])['test'][:6]
    independent=[{**r,'id':'independent_'+r['id']} for r in zero.make_cases(190731,96,36,120)['test'][:n-6]]
    assert len({abs(r['a']) for r in original+independent})==n,'ALIGN_REPAIR_DUPLICATE_TEST_MAGNITUDES'
    return original+independent


def run(*,stage='all',device='cuda',base=interface.hybrid.DEFAULT_OUTPUT,
        numerical=zero.DEFAULT_OUTPUT,interface_dir=interface.ROOT,output=ROOT,
        steps=240,eval_every=20,cases=18,budget=48,resume=False,stop_after=None,
        loader=None,allow_download=False):
    assert stage in ('all','calibrate','train','recurrence','gate'),'ALIGN_REPAIR_BAD_STAGE'
    base,numerical,interface_dir,output=map(Path,(base,numerical,interface_dir,output))
    _verify_output_root(output,base,numerical,interface_dir)
    hashes=_sources(interface_dir)
    if stage=='calibrate' or (stage=='all' and not (output/'calibration.json').exists()):
        assert not (output/'calibration.json').exists(),'ALIGN_REPAIR_CALIBRATION_EXISTS'
        model,tok,fc,h=_load(device=device,base=base,numerical=numerical,
            interface_dir=interface_dir,loader=loader,allow_download=allow_download)
        assert h==hashes
        output.mkdir(parents=True,exist_ok=True)
        _run_calibrate(model,tok,fc,interface_dir,output,hashes,device)
        if stage=='calibrate':return 0
        del model
        if device=='cuda':torch.cuda.empty_cache()
    if stage in ('all','train'):
        model,tok,fc,h=_load(device=device,base=base,numerical=numerical,
            interface_dir=interface_dir,loader=loader,allow_download=allow_download)
        result=_train(model,tok,fc,hashes,output,device,steps=steps,eval_every=eval_every,
            resume=resume,stop_after=stop_after,interface_dir=interface_dir)
        if result['status']!='TRAINED_UNCERTIFIED' or stage=='train':return 0
        del model
        if device=='cuda':torch.cuda.empty_cache()
    if stage in ('all','recurrence','gate'):
        model,tok,fc,h=_load(device=device,base=base,numerical=numerical,
            interface_dir=interface_dir,loader=loader,allow_download=allow_download)
        learned,cp=_restore(model,output,hashes)
        model.head=learned
        model.eval()
        # Refuse if calibrated geometry got WORSE than pretraining on DEV.
        dev=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])['dev']
        dev_pairs=interface._pairs(model,tok,dev,device,fc['max_seq'])
        actual=_metrics(learned,dev_pairs)
        earlier=_json(output/'alignment'/'result.json')['prior']
        geometry_ok=(actual['relative_mse_to_zero']<1. and
                     actual['non_exit_cosine']>=earlier['non_exit_cosine'] and
                     0.5<=actual['median_norm_ratio']<=2.)
        print('HYBRID_ALIGN_GEOMETRY_GATE='+json.dumps({'pass':geometry_ok,'dev':actual,'baseline':earlier},sort_keys=True),flush=True)
        if not geometry_ok:
            zero.atomic_json(output/'geometry_gate.json',{'schema':SCHEMA,'pass':False,'dev':actual,'baseline':earlier})
            return 3
        zero.atomic_json(output/'geometry_gate.json',{'schema':SCHEMA,'pass':True,'dev':actual,'baseline':earlier})
        if stage in ('all','recurrence'):
            tests=_gate_rows(fc,6)
            cache=cache_equivalence(model,tok,tests,device=device,head=learned)
            cache['repair_head_sha256']=interface.fingerprint(output/'alignment'/'best.pt')
            cache['source_checkpoints']=hashes
            zero.atomic_json(output/'recurrence.json',cache)
            print('HYBRID_ALIGN_RECURRENCE='+json.dumps({k:v for k,v in cache.items() if k!='records'},sort_keys=True),flush=True)
            if not cache['pass']:return 3
            if stage=='recurrence':return 0
        if stage=='gate':
            checked=_json(output/'recurrence.json')
            assert checked['schema']==SCHEMA and checked['pass'] is True,'ALIGN_REPAIR_RECURRENCE_NOT_PASSED'
            assert checked['repair_head_sha256']==interface.fingerprint(output/'alignment'/'best.pt') and checked['source_checkpoints']==hashes, 'ALIGN_REPAIR_RECURRENCE_CHECKPOINT_DRIFT'
        rows=_gate_rows(fc,cases)
        report=behavioural_gate(model,tok,learned,rows,device=device,budget=budget)
        assert _sources(interface_dir)==hashes,'ALIGN_REPAIR_SOURCES_MUTATED'
        result={'schema':SCHEMA,'status':'ONE_STEP_PASS' if report['latent_advantage_pass'] else 'ONE_STEP_FAIL',
                'cases':cases,'score':report['score'],'by_group':report['by_group'],'criteria':report['criteria'],
                'blind_set_fingerprint':zero.fingerprint(rows),
                'latent_advantage_pass':report['latent_advantage_pass'],'records':report['records'],
                'sources':hashes,'new_head_checkpoint_sha256':interface.fingerprint(output/'alignment'/'best.pt'),
                'oracle_latents_supplied':0,'deeper_training_permitted':bool(report['latent_advantage_pass'])}
        zero.atomic_json(output/'gate_result.json',result)
        print('HYBRID_ALIGN_GATE_RESULT='+json.dumps({k:v for k,v in result.items() if k!='records'},sort_keys=True),flush=True)
        return 0 if report['latent_advantage_pass'] else 3
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','calibrate','train','recurrence','gate'),default='all')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--baseline',type=Path,default=interface.hybrid.DEFAULT_OUTPUT)
    p.add_argument('--numerical',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--interface',type=Path,default=interface.ROOT)
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--steps',type=int,default=240)
    p.add_argument('--eval-every',type=int,default=20)
    p.add_argument('--cases',type=int,default=18)
    p.add_argument('--max-new-tokens',type=int,default=48)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int)
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        return run(stage=args.stage,device=args.device,base=args.baseline,
            numerical=args.numerical,interface_dir=args.interface,output=args.output,
            steps=args.steps,eval_every=args.eval_every,cases=args.cases,
            budget=args.max_new_tokens,resume=args.resume,stop_after=args.stop_after,
            allow_download=args.allow_model_download)
    except (AssertionError,RuntimeError,ValueError,KeyError,FileNotFoundError,OSError) as exc:
        print('HYBRID_ALIGN_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':sys.exit(main())
