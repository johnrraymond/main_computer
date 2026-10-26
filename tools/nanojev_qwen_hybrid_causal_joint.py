#!/usr/bin/env python3
"""Answer-supervised, task-specific one-latent HybridSFT for Qwen3-0.6B.

No oracle/teacher vector enters the latent path. A detached prompt pass makes
one self-generated latent; a second, differentiable Qwen pass trains HybridLink
and attention LoRA from the WHOLE target answer. Genuine arithmetic trajectories
provide auxiliary teacher-forced supervision only in training. New checkpoints
never replace the certified format or earlier aligned head lineage.

The two-pass stop-gradient source path is an explicit 6-GB VRAM compromise,
not an exact reproduction of full-gradient HybridAgent/HybridSFT.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

import torch
from torch.nn import functional as F

from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybrid_alignment_repair as align
from tools import nanojev_qwen_hybrid_interface_repair as interface

SCHEMA='nanojev.qwen3-06b.hybrid-causal-joint.v1'
ROOT=Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_causal_joint_v1'


def actual_reasoning(row):
    """Task-specific, arithmetically checked teacher TARGET, never inference input."""
    a,u,v,y=(int(row[k]) for k in ('a','u','v','answer'))
    assert u+v==y,'CAUSAL_TEACHER_ARITHMETIC_INCONSISTENT'
    assert -a+a==0,'CAUSAL_TEACHER_CANCEL_INCONSISTENT'
    return (f'Cancel ({a}) with ({-a}) to obtain 0. '
            f'Then ({u}) + ({v}) = ({y}).')


def make_data(seed,train_count,dev_count,test_count):
    assert all(x>=6 and x%6==0 for x in (train_count,dev_count,test_count)),'CAUSAL_UNBALANCED_COUNTS'
    data=zero.make_cases(seed,train_count,dev_count,test_count)
    assert len({r['expression'] for group in data.values() for r in group})==sum(map(len,data.values())),'CAUSAL_DUPLICATE_EXPRESSIONS'
    for split in data:
        for row in data[split]:actual_reasoning(row)
    return data


def check_inputs(output,base,numerical,interface_dir,repair_root):
    output=Path(output).resolve()
    for source in (base,numerical,interface_dir,repair_root):
        s=Path(source).resolve()
        assert output!=s and output not in s.parents and s not in output.parents,'CAUSAL_OUTPUT_OVERLAPS_SOURCE'


def provenance(interface_dir,repair_root):
    # Intentionally accepts the previous ONE_STEP_FAIL. It is evidence, not a
    # reason to forbid controlled experimental training in a NEW lineage.
    prior=align._sources(interface_dir)
    report=align._json(Path(repair_root)/'gate_result.json')
    geom=align._json(Path(repair_root)/'geometry_gate.json')
    recurrence=align._json(Path(repair_root)/'recurrence.json')
    sha=interface.fingerprint(Path(repair_root)/'alignment'/'best.pt')
    assert report['schema']==align.SCHEMA and report['sources']==prior,'CAUSAL_PRIOR_GATE_DRIFT'
    assert report['status'] in ('ONE_STEP_FAIL','ONE_STEP_PASS'),'CAUSAL_PRIOR_GATE_INVALID'
    assert report['new_head_checkpoint_sha256']==sha,'CAUSAL_PRIOR_HEAD_CHANGED'
    assert geom.get('pass') is True and recurrence.get('pass') is True,'CAUSAL_GEOMETRY_RECURRENCE_REQUIRED'
    assert recurrence['repair_head_sha256']==sha and recurrence['source_checkpoints']==prior,'CAUSAL_RECURRENCE_DRIFT'
    assert align._json(Path(repair_root)/'alignment'/'result.json')['status']=='TRAINED_UNCERTIFIED','CAUSAL_ALIGNMENT_INCOMPLETE'
    return {'format':prior['format'],'previous_head':prior['head'],'repaired_head':sha,
            'previous_gate':interface.fingerprint(Path(repair_root)/'gate_result.json'),
            'previous_geometry':interface.fingerprint(Path(repair_root)/'geometry_gate.json'),
            'previous_recurrence':interface.fingerprint(Path(repair_root)/'recurrence.json')}


def frozen_prompt_hidden(model,prompt_ids):
    """Self-produced source from question only. No teacher thought or answer."""
    with torch.no_grad():
        embeds=model.backbone.get_input_embeddings()(prompt_ids)
        state=model.backbone.get_decoder()(inputs_embeds=embeds,
            attention_mask=torch.ones_like(prompt_ids),use_cache=False,
            return_dict=True).last_hidden_state[:,-1,:]
    return state.detach()


def prompt_tokens(tok,model,row,device):
    prompt='Question: Compute '+row['expression']+'.\nReasoning: '
    ids=tok(prompt,add_special_tokens=False)['input_ids']
    assert ids and model.start_id != model.end_id,'CAUSAL_BAD_PROMPT'
    return torch.tensor([ids+[model.start_id]],dtype=torch.long,device=device)


def suffix_ids(tok,kind):
    assert kind in ('answer','reasoning'),'CAUSAL_UNKNOWN_SUPERVISION'
    prefix='\nAnswer: ' if kind=='answer' else '\nReasoning: '
    ids=tok(prefix,add_special_tokens=False)['input_ids']
    assert ids,'CAUSAL_EMPTY_SUFFIX'
    return ids


def target_tokens(tok,row,kind):
    assert kind in ('answer','reasoning'),'CAUSAL_UNKNOWN_SUPERVISION'
    if kind=='answer':
        continuation=str(row['answer'])+'\n'
    else:
        continuation=actual_reasoning(row)+'\n'
    s=suffix_ids(tok,kind)
    t=tok(continuation,add_special_tokens=False)['input_ids']
    assert s and t,'CAUSAL_EMPTY_TARGET'
    return s,t


def second_pass_logprob(model,tok,row,source_hidden,*,kind='answer',latent=None,max_seq=192):
    """Full multi-token teacher-forced CE, not a first-answer-token proxy.

    Gold continuation is standard LM supervision: each token is predicted
    from preceding tokens only. It never contributes to source_hidden/latent.
    """
    device=source_hidden.device
    marked=prompt_tokens(tok,model,row,device)
    suffix,targets=target_tokens(tok,row,kind)
    if latent is None:latent=model.head(source_hidden.float())
    assert latent.shape==(1,source_hidden.shape[-1]),'CAUSAL_LATENT_SHAPE'
    assert torch.isfinite(latent).all(),'CAUSAL_LATENT_NONFINITE'
    assert marked.size(1)+2+len(suffix)+len(targets)<=max_seq,'CAUSAL_SEQUENCE_TOO_LONG'
    ordinary=torch.tensor([[model.end_id]+suffix+targets[:-1]],device=device,dtype=torch.long)
    embed=model.backbone.get_input_embeddings()
    pieces=(embed(marked),latent[:,None,:].to(embed.weight.dtype),embed(ordinary))
    joined=torch.cat(pieces,dim=1)
    attention=torch.ones((1,joined.size(1)),device=device,dtype=torch.long)
    state=model.backbone.get_decoder()(inputs_embeds=joined,attention_mask=attention,
        use_cache=False,return_dict=True).last_hidden_state
    # Last suffix embedding predicts the first answer token. Subsequent answer
    # tokens are teacher-forced (strictly preceding) as in ordinary LM CE.
    beginning=marked.size(1)+1+1+len(suffix)-1
    positions=torch.arange(beginning,beginning+len(targets),device=device)
    hidden=state[0].index_select(0,positions)
    output=model.backbone.get_output_embeddings()
    logits=output(hidden.to(output.weight.dtype)).float()
    labels=torch.tensor(targets,device=device,dtype=torch.long)
    ce=F.cross_entropy(logits,labels,reduction='mean')
    assert torch.isfinite(ce),'CAUSAL_CE_NONFINITE'
    return {'loss':ce,'tokens':len(targets),'logprob':-ce}


def train_sample(model,tok,row,device,*,kind='answer',max_seq=192,negative=None,rank_weight=0.35):
    marked=prompt_tokens(tok,model,row,device)
    source=frozen_prompt_hidden(model,marked)
    intact=second_pass_logprob(model,tok,row,source,kind=kind,max_seq=max_seq)
    combined=intact['loss']
    ranking=None
    if negative is not None:
        assert kind=='answer' and negative['answer']!=row['answer'],'CAUSAL_BAD_NEGATIVE'
        # A real mismatched SELF-GENERATED latent (not a teacher oracle).
        other=frozen_prompt_hidden(model,prompt_tokens(tok,model,negative,device))
        with torch.no_grad():
            wrong=model.head(other.float()).detach()
            zeroed=torch.zeros_like(wrong)
            wrong_lp=second_pass_logprob(model,tok,row,source,kind='answer',latent=wrong,max_seq=max_seq)['logprob']
            zero_lp=second_pass_logprob(model,tok,row,source,kind='answer',latent=zeroed,max_seq=max_seq)['logprob']
        # Ranking pushes task-specific signal into intact feedback; wrong/zero
        # paths are fixed controls, not sources of gold inputs or gradients.
        ranking=(F.softplus(0.20+wrong_lp-intact['logprob'])+
                 F.softplus(0.20+zero_lp-intact['logprob']))/2
        combined=combined+rank_weight*ranking
    return {'loss':combined,'ce':intact['loss'],'ranking':ranking,
            'tokens':intact['tokens']}


def param_scope(model):
    lora=zero.lora_named_parameters(model.backbone)
    head=[(name,p) for name,p in model.head.named_parameters() if p.requires_grad]
    assert lora and head,'CAUSAL_MISSING_TRAINABLE_GROUP'
    assert all(p.requires_grad for _,p in lora+head),'CAUSAL_FROZEN_TRAINABLE_PARAM'
    assert all(not p.requires_grad for n,p in model.backbone.named_parameters()
               if not n.endswith(('.lora_a','.lora_b'))),'CAUSAL_BASE_UNFROZEN'
    return lora,head


def dev_score(model,tok,rows,device,max_seq):
    model.eval()
    nll=0.
    with torch.no_grad():
        for row in rows:
            s=frozen_prompt_hidden(model,prompt_tokens(tok,model,row,device))
            nll+=float(second_pass_logprob(model,tok,row,s,max_seq=max_seq)['loss'])
    return nll/len(rows)


def _state(model):
    return {'head':{k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()},
            'lora':zero.lora_state(model.backbone)}


def _restore_state(model,state):
    model.head.load_state_dict(state['head'],strict=True)
    zero.restore_lora(model.backbone,state['lora'])


def _load_model(device,base,numerical,interface_dir,repair_root,loader=None,allow_download=False):
    model,tok,fc,old=align._load(device=device,base=base,numerical=numerical,
        interface_dir=interface_dir,loader=loader,allow_download=allow_download)
    head,best=align._restore(model,repair_root,old)
    model.head=head
    lora,params=param_scope_after_enable(model)
    assert best['schema']==align.SCHEMA,'CAUSAL_UNEXPECTED_INITIAL_HEAD'
    return model,tok,fc,old


def param_scope_after_enable(model):
    model.backbone.requires_grad_(False)
    # The restored format-stage LoRA remains the starting adapter. This trains
    # ONLY those existing q/v projection LoRAs, never the base Qwen weights.
    lora=zero.lora_named_parameters(model.backbone)
    for _,p in lora:p.requires_grad_(True)
    model.head.requires_grad_(True)
    return param_scope(model)


def select_negative(rows,index):
    row=rows[index%len(rows)]
    for shift in range(1,len(rows)):
        other=rows[(index+shift)%len(rows)]
        if other['answer']!=row['answer']:
            return other
    raise AssertionError('CAUSAL_NO_DISTINCT_NEGATIVE')


def _config(sources,data,steps,eval_every,seed,train_count,dev_count,test_count,
            max_seq,head_lr,lora_lr,rank_weight,aux_every):
    return {'schema':SCHEMA,'sources':sources,'data_fingerprint':zero.fingerprint(data),
      'algorithm':'two-pass-detached-source;self-generated-latent;answer-full-token-CE;real-arithmetic-target-aux;zero-and-cross-example-ranking',
      'steps':steps,'eval_every':eval_every,'seed':seed,'train_count':train_count,
      'dev_count':dev_count,'test_count':test_count,'max_seq':max_seq,
      'head_lr':head_lr,'lora_lr':lora_lr,'rank_weight':rank_weight,
      'aux_every':aux_every,'base_model':'Qwen/Qwen3-0.6B-Base',
      'test_selection_forbidden':True,'stopgrad_source_pass':True}


def train(*,device='cuda',base=interface.hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
          interface_dir=interface.ROOT,repair_root=align.ROOT,output=ROOT,
          steps=240,eval_every=20,seed=2027,train_count=96,dev_count=18,test_count=18,
          max_seq=192,head_lr=3e-5,lora_lr=5e-6,rank_weight=0.35,aux_every=4,
          resume=False,stop_after=None,loader=None,allow_download=False):
    base,numerical,interface_dir,repair_root,output=map(Path,(base,numerical,interface_dir,repair_root,output))
    check_inputs(output,base,numerical,interface_dir,repair_root)
    assert 1<=steps<=5000 and 1<=eval_every<=steps and max_seq>=48,'CAUSAL_BAD_STEPS_OR_SHAPE'
    assert 0<head_lr<1e-2 and 0<lora_lr<1e-2 and 0<=rank_weight<=5 and 1<=aux_every<=100,'CAUSAL_BAD_HYPERPARAMETERS'
    assert stop_after is None or 1<=stop_after<=steps,'CAUSAL_BAD_STOP'
    sources=provenance(interface_dir,repair_root)
    data=make_data(seed,train_count,dev_count,test_count)
    cfg=_config(sources,data,steps,eval_every,seed,train_count,dev_count,test_count,
                max_seq,head_lr,lora_lr,rank_weight,aux_every)
    cfg_hash=zero.fingerprint(cfg)
    folder=output/'training'
    if resume:
        assert (folder/'config.json').is_file() and (folder/'last.pt').is_file(),'CAUSAL_RESUME_MISSING'
        assert align._json(folder/'config.json')==cfg,'CAUSAL_RESUME_CONFIG_DRIFT'
    else:
        assert not folder.exists() or not any(folder.iterdir()),'CAUSAL_TRAIN_EXISTS'
        folder.mkdir(parents=True,exist_ok=True)
        zero.atomic_json(folder/'config.json',cfg)
    model,tok,fc,source_hashes=_load_model(device,base,numerical,interface_dir,repair_root,
        loader=loader,allow_download=allow_download)
    assert source_hashes['format']==sources['format'] and source_hashes['head']==sources['previous_head'],'CAUSAL_SOURCE_MISMATCH'
    # Preserve all original frozen base weights; never persist an overwritten
    # pretrained file. No old checkpoint is modified by this experiment.
    params_head=[p for p in model.head.parameters() if p.requires_grad]
    params_lora=[p for _,p in zero.lora_named_parameters(model.backbone)]
    optimizer=torch.optim.AdamW([{'params':params_head,'lr':head_lr},
       {'params':params_lora,'lr':lora_lr}],weight_decay=0.)
    trainable_ids={id(p) for group in optimizer.param_groups for p in group['params']}
    assert trainable_ids=={id(p) for p in params_head+params_lora},'CAUSAL_OPTIMIZER_SCOPE_DRIFT'
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed)
    if resume:
        cp=torch.load(folder/'last.pt',map_location='cpu',weights_only=True)
        assert cp['schema']==SCHEMA and cp['config_hash']==cfg_hash and 0<cp['step']<steps,'CAUSAL_RESUME_INVALID'
        _restore_state(model,cp['state']);optimizer.load_state_dict(cp['optimizer'])
        torch.set_rng_state(cp['torch_rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(cp['cuda_rng'])
        start=int(cp['step']);best=float(cp['best_dev_ce'])
    else:start=0;best=float('inf')
    original_frozen=zero.frozen_backbone_sha256(model.backbone)
    summary={}
    for step in range(start+1,steps+1):
        row=data['train'][(step-1)%train_count]
        neg=select_negative(data['train'],(step-1)%train_count) if step%2==0 else None
        optimizer.zero_grad(set_to_none=True)
        # Backbone in eval mode disables dropout, but leaves LoRA gradients on.
        model.eval()
        losses=train_sample(model,tok,row,device,max_seq=max_seq,negative=neg,
                            rank_weight=rank_weight)
        losses['loss'].backward()
        if step%aux_every==0:
            aux=train_sample(model,tok,row,device,kind='reasoning',max_seq=max_seq)
            (0.2*aux['loss']).backward()
        else:aux=None
        groups=param_scope(model)
        for group_name,group in zip(('lora','head'),groups):
            nonzero=[n for n,p in group if p.grad is not None and torch.isfinite(p.grad).all() and bool((p.grad!=0).any())]
            assert nonzero,'CAUSAL_NO_GRAD_'+group_name
            assert all(p.grad is None or bool(torch.isfinite(p.grad).all()) for _,p in group),'CAUSAL_NONFINITE_GRAD_'+group_name
        torch.nn.utils.clip_grad_norm_(params_head+params_lora,1.)
        optimizer.step()
        if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
            dc=dev_score(model,tok,data['dev'],device,max_seq)
            if dc<best:
                best=dc
                zero.atomic_torch(folder/'best.pt',{'schema':SCHEMA,'config_hash':cfg_hash,
                 'step':step,'state':_state(model),'dev_ce':dc})
            summary={'step':step,'total':steps,'answer_ce':float(losses['ce'].detach()),
                 'ranking':float(losses['ranking'].detach()) if losses['ranking'] is not None else None,
                 'reasoning_ce':float(aux['ce'].detach()) if aux is not None else None,
                 'dev_answer_ce':dc,'best_dev_ce':best,'trainable_lora':len(params_lora)}
            zero.atomic_torch(folder/'last.pt',{'schema':SCHEMA,'config_hash':cfg_hash,
                'step':step,'state':_state(model),'optimizer':optimizer.state_dict(),
                'torch_rng':torch.get_rng_state(),
                'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None,
                'best_dev_ce':best})
            print('HYBRID_CAUSAL_STEP='+json.dumps(summary,sort_keys=True),flush=True)
        if stop_after and step>=stop_after:break
    assert zero.frozen_backbone_sha256(model.backbone)==original_frozen,'CAUSAL_FROZEN_BACKBONE_MUTATION'
    assert provenance(interface_dir,repair_root)==sources,'CAUSAL_SOURCE_MUTATION'
    status='TRAINED_UNCERTIFIED' if step==steps else 'PAUSED_RESUMABLE'
    result={'schema':SCHEMA,'status':status,'steps':steps,'step':step,
        'dev_best_ce':best,'checkpoint':str(folder/'best.pt'),
        'backbone_base_mutations':0,'sources':sources,'optimizer_updates':step,'last':summary,
        'deeper_training_permitted':False}
    zero.atomic_json(folder/'result.json',result)
    print('HYBRID_CAUSAL_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def _load_trained(device,base,numerical,interface_dir,repair_root,output,loader=None,allow_download=False):
    sources=provenance(interface_dir,repair_root)
    cfg=align._json(output/'training'/'config.json')
    result=align._json(output/'training'/'result.json')
    assert cfg['schema']==SCHEMA and cfg['sources']==sources and result['status']=='TRAINED_UNCERTIFIED','CAUSAL_TRAIN_NOT_COMPLETE'
    model,tok,fc,initial=_load_model(device,base,numerical,interface_dir,repair_root,
        loader=loader,allow_download=allow_download)
    cp=torch.load(output/'training'/'best.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==SCHEMA and cp['config_hash']==zero.fingerprint(cfg),'CAUSAL_BEST_CONFIG_DRIFT'
    _restore_state(model,cp['state'])
    model.eval().requires_grad_(False)
    return model,tok,cfg,sources,cp


def _prefix(model,tok,row,condition,shuffled=None):
    assert condition in ('intact','zero','shuffled','marker_only','text'),'CAUSAL_BAD_CONDITION'
    device=next(model.head.parameters()).device
    ids=prompt_tokens(tok,model,row,device)
    if condition=='text':
        ids=ids[:,:-1] # no latent marker
        suffix=torch.tensor([suffix_ids(tok,'answer')],device=device,dtype=torch.long)
        ids=torch.cat((ids,suffix),dim=1)
        mask=torch.ones_like(ids)
        _,logits,cache=model._step(input_ids=ids,mask=mask)
        return logits,cache,mask
    if condition=='marker_only':
        seq=ids.new_tensor([[model.end_id]+suffix_ids(tok,'answer')])
        ids=torch.cat((ids,seq),dim=1)
        mask=torch.ones_like(ids)
        _,logits,cache=model._step(input_ids=ids,mask=mask)
        return logits,cache,mask
    mask=torch.ones_like(ids)
    hidden,_,cache=model._step(input_ids=ids,mask=mask)
    if condition=='intact':vector=model.head(hidden.float())
    elif condition=='zero':vector=torch.zeros_like(model.head(hidden.float()))
    else:
        assert shuffled is not None,'CAUSAL_SHUFFLE_MISSING'
        with torch.no_grad():
            h,_,_=model._step(input_ids=prompt_tokens(tok,model,shuffled,device),
                mask=torch.ones_like(prompt_tokens(tok,model,shuffled,device)))
            vector=model.head(h.float())
    mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
    _,_,cache=model._step(inputs_embeds=vector[:,None,:],cache=cache,mask=mask)
    continuation=ids.new_tensor([[model.end_id]+suffix_ids(tok,'answer')])
    mask=torch.cat((mask,mask.new_ones((1,continuation.size(1)))),dim=1)
    _,logits,cache=model._step(input_ids=continuation,cache=cache,mask=mask)
    return logits,cache,mask


def decode(model,tok,row,condition,*,negative=None,budget=24):
    assert budget>0
    with torch.inference_mode():
        logits,cache,mask=_prefix(model,tok,row,condition,negative)
        output=[]
        for _ in range(budget):
            token=int(logits.argmax(dim=-1).item())
            if tok.eos_token_id is not None and token==tok.eos_token_id:break
            output.append(token)
            nxt=mask.new_tensor([[token]])
            mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
            _,logits,cache=model._step(input_ids=nxt,cache=cache,mask=mask)
        completion=tok.decode(output,skip_special_tokens=True)
        # Parse ONLY the start of the answer continuation, not an answer label
        # hallucinated much later in unrelated generated text.
        match=re.match(r'^\s*([+-]?\d+)(?!\d)',completion)
        predicted=int(match.group(1)) if match else None
        return {'answer':predicted,'correct':predicted==row['answer'],
                'parseable':predicted is not None,'completion':completion[:220],
                'tokens':len(output)}


def blind_gate(*,device='cuda',base=interface.hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
        interface_dir=interface.ROOT,repair_root=align.ROOT,output=ROOT,
        budget=24,loader=None,allow_download=False):
    base,numerical,interface_dir,repair_root,output=map(Path,(base,numerical,interface_dir,repair_root,output))
    check_inputs(output,base,numerical,interface_dir,repair_root)
    assert 1<=budget<=100,'CAUSAL_BAD_BUDGET'
    assert not (output/'gate_started.json').exists() and not (output/'gate_result.json').exists(),'CAUSAL_GATE_ALREADY_EXISTS'
    model,tok,cfg,sources,cp=_load_trained(device,base,numerical,interface_dir,
        repair_root,output,loader=loader,allow_download=allow_download)
    data=make_data(cfg['seed'],cfg['train_count'],cfg['dev_count'],cfg['test_count'])
    assert zero.fingerprint(data)==cfg['data_fingerprint'],'CAUSAL_DATA_DRIFT'
    rows=data['test']
    # A started blind test is consumed even if execution is interrupted. This
    # prevents adaptive reruns or opportunistic test-set checkpoint selection.
    zero.atomic_json(output/'gate_started.json',{'schema':SCHEMA,
        'best_sha256':interface.fingerprint(output/'training'/'best.pt'),
        'test_split_fingerprint':zero.fingerprint(rows),
        'status':'BLIND_SET_OPENED_ONCE'})
    results=[]
    with torch.inference_mode():
        for i,row in enumerate(rows):
            # Construct control without consulting the case's gold answer.
            bad=rows[(i+1)%len(rows)]
            entry={'id':row['id'],'expected':row['answer'],'family':row['family'],'sign':row['sign'],'variants':{}}
            for condition in ('intact','zero','shuffled','marker_only','text'):
                entry['variants'][condition]=decode(model,tok,row,condition,negative=bad,budget=budget)
            results.append(entry)
            print('HYBRID_CAUSAL_CASE='+json.dumps(entry,sort_keys=True),flush=True)
    counts={name:sum(r['variants'][name]['correct'] for r in results)
          for name in ('intact','zero','shuffled','marker_only','text')}
    successes=(counts['intact']>=counts['zero']+2 and
               counts['intact']>=counts['shuffled']+2 and
               counts['intact']>=counts['text']-1 and
               sum(r['variants']['intact']['parseable'] for r in results)>=len(results)-1)
    report={'schema':SCHEMA,'status':'ONE_STEP_PASS' if successes else 'ONE_STEP_FAIL',
            'cases':len(results),'score':counts,'causal_advantage_pass':bool(successes),
            'deeper_training_permitted':bool(successes),'oracle_latents_supplied_at_inference':0,
            'gold_answers_used_in_training':True,'teacher_thoughts_used_at_inference':False,
            'test_split_only_once':True,'sources':sources,
            'joint_best_checkpoint_sha256':interface.fingerprint(output/'training'/'best.pt'),
            'training_config_hash':zero.fingerprint(cfg),
            'criteria':{'min_over_zero':2,'min_over_shuffled':2,'max_regression_against_text':1,
                        'max_unparseable':1},'records':results}
    zero.atomic_json(output/'gate_result.json',report)
    print('HYBRID_CAUSAL_GATE_RESULT='+json.dumps({k:v for k,v in report.items() if k!='records'},sort_keys=True),flush=True)
    assert provenance(interface_dir,repair_root)==sources,'CAUSAL_SOURCE_MUTATION_AFTER_GATE'
    return report


# CLI arguments are a superset of each stage's keyword parameters. Explicitly
# route them rather than forwarding the full namespace into train(): --budget
# belongs to blind_gate only, and training options must never reach the gate.
_TRAIN_OPTIONS=frozenset(('device','base','numerical','interface_dir','repair_root','output',
    'steps','eval_every','seed','train_count','dev_count','test_count','max_seq',
    'head_lr','lora_lr','rank_weight','aux_every','resume','stop_after',
    'loader','allow_download'))
_GATE_OPTIONS=frozenset(('device','base','numerical','interface_dir','repair_root','output',
    'budget','loader','allow_download'))


def run(*,stage='all',**kwargs):
    assert stage in ('all','train','gate'),'CAUSAL_INVALID_STAGE'
    unknown=set(kwargs)-(_TRAIN_OPTIONS|_GATE_OPTIONS)
    if unknown:
        raise ValueError('CAUSAL_UNKNOWN_OPTION: '+', '.join(sorted(unknown)))
    if stage in ('all','train'):
        train_args={k:v for k,v in kwargs.items() if k in _TRAIN_OPTIONS}
        out=train(**train_args)
        if stage=='train' or out['status']!='TRAINED_UNCERTIFIED':return out
    gate_args={k:v for k,v in kwargs.items() if k in _GATE_OPTIONS}
    return blind_gate(**gate_args)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','train','gate'),default='all')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--steps',type=int,default=240)
    p.add_argument('--eval-every',type=int,default=20)
    p.add_argument('--stop-after',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--seed',type=int,default=2027)
    p.add_argument('--train-count',type=int,default=96)
    p.add_argument('--dev-count',type=int,default=18)
    p.add_argument('--test-count',type=int,default=18)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--head-lr',type=float,default=3e-5)
    p.add_argument('--lora-lr',type=float,default=5e-6)
    p.add_argument('--rank-weight',type=float,default=0.35)
    p.add_argument('--aux-every',type=int,default=4)
    p.add_argument('--budget',type=int,default=24)
    p.add_argument('--base',type=Path,default=interface.hybrid.DEFAULT_OUTPUT)
    p.add_argument('--numerical',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--interface-dir',type=Path,default=interface.ROOT)
    p.add_argument('--repair-root',type=Path,default=align.ROOT)
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--allow-model-download',action='store_true')
    a=p.parse_args(argv)
    kw=vars(a);stage=kw.pop('stage');kw['allow_download']=kw.pop('allow_model_download')
    try:
        report=run(stage=stage,**kw)
        print('HYBRID_CAUSAL_RESULT='+json.dumps({k:v for k,v in report.items() if k!='records'},sort_keys=True),flush=True)
    except (AssertionError,RuntimeError,ValueError,FileNotFoundError,OSError) as exc:
        print('HYBRID_CAUSAL_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
