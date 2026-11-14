#!/usr/bin/env python3
"""Question-hidden HybridLink information-channel certification and protected restoration.

Stages: gradient -> channel -> qualify -> restore -> gate. 'all' executes
only authorized stages, refusing after a failed qualification. Every vector
is self-produced from the question; the decoder in 'channel' sees none of
that question, just a fixed public template and the continuous latent.
The source hidden-state pass is detached for 6-GB VRAM two-pass training.
This is an information-transfer test, not a proof of arithmetic reasoning.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import torch
from torch.nn import functional as F

from tools import nanojev_qwen_hybrid_causal_joint as joint
from tools import nanojev_qwen_hybrid_interface_repair as interface
from tools import nanojev_qwen_zero_line_backbone as zero

SCHEMA = 'nanojev.qwen3-06b.hybrid-bottleneck.v1'
ROOT = Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_bottleneck_v1'
CONSUMER_TEXT = 'Question: [private]\nReasoning: '


def data_splits(seed=2041, train_count=96, dev_count=18, qualify_count=18, test_count=18):
    assert all(x >= 6 and x % 6 == 0 for x in (train_count, dev_count, qualify_count, test_count)), 'BOT_BAD_COUNTS'
    data=joint.make_data(seed, train_count, dev_count, qualify_count+test_count)
    # Independent examples, including all six (sign, family) classes in BOTH
    # test partitions. The final partition is never consulted by qualification.
    tests=data.pop('test')
    data['qualify']=tests[:qualify_count]
    data['test']=tests[qualify_count:]
    for key in data:
        assert len({r['expression'] for r in data[key]}) == len(data[key]),'BOT_SPLIT_DUPLICATE'
    return data


def source_guard(output, base, numerical, interface_dir, repair_root, joint_root):
    output=Path(output).resolve()
    joint.check_inputs(output,base,numerical,interface_dir,repair_root)
    source_paths=[Path(joint_root).resolve()]
    assert all(output!=p and output not in p.parents and p not in output.parents for p in source_paths),'BOT_OUTPUT_OVERLAPS_SOURCE'


def source_provenance(interface_dir, repair_root, joint_root):
    sources=joint.provenance(interface_dir,repair_root)
    cfg=joint.align._json(Path(joint_root)/'training'/'config.json')
    train=joint.align._json(Path(joint_root)/'training'/'result.json')
    gate=joint.align._json(Path(joint_root)/'gate_result.json')
    chk=Path(joint_root)/'training'/'best.pt'
    sha=interface.fingerprint(chk)
    assert train['schema']==joint.SCHEMA and train['status']=='TRAINED_UNCERTIFIED','BOT_JOINT_TRAIN_MISSING'
    assert gate['schema']==joint.SCHEMA and gate['status'] in ('ONE_STEP_FAIL','ONE_STEP_PASS'),'BOT_JOINT_GATE_MISSING'
    assert gate['joint_best_checkpoint_sha256']==sha,'BOT_JOINT_CHECKPOINT_DRIFT'
    assert cfg['sources']==sources and train['sources']==sources and gate['sources']==sources,'BOT_JOINT_SOURCE_DRIFT'
    return {'joint_best':sha,'joint_gate':interface.fingerprint(Path(joint_root)/'gate_result.json'),
            'joint_config':zero.fingerprint(cfg),'previous':sources}


def load_trainable(device,base,numerical,interface_dir,repair_root,joint_root,
                   *,loader=None,allow_download=False):
    model,tok,_,_=joint._load_model(device,base,numerical,interface_dir,repair_root,
                                     loader=loader,allow_download=allow_download)
    cp=torch.load(Path(joint_root)/'training'/'best.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==joint.SCHEMA,'BOT_JOINT_STATE_SCHEMA'
    joint._restore_state(model,cp['state'])
    joint.param_scope_after_enable(model)
    return model,tok


def hidden_from_question(model,tok,row,device):
    """No gold answer, teacher text or target continuation enters producer."""
    ids=joint.prompt_tokens(tok,model,row,device)
    return joint.frozen_prompt_hidden(model,ids)


def fixed_consumer_ids(model,tok,device):
    ids=tok(CONSUMER_TEXT,add_special_tokens=False)['input_ids']
    assert ids and model.start_id!=model.end_id,'BOT_CONSUMER_BAD_MARKERS'
    return torch.tensor([ids+[model.start_id]],device=device,dtype=torch.long)


def consumer_ce(model,tok,row,hidden,*,condition='intact',wrong_hidden=None,max_seq=192,
                track_vector=False):
    """Full-answer CE with no question/operands or teacher reasoning at consumer.

    Feed answer tokens strictly after the constant consumer prefix and latent.
    A source_hidden from another problem is a self-generated mismatched vector,
    not an oracle. For 'zero' the entire vector is zeroed; both controls use
    identical fixed text, suffix, marker count and sequence positions.
    """
    assert condition in ('intact','zero','shuffled'),'BOT_BAD_CONDITION'
    device=hidden.device
    source=hidden if condition!='shuffled' else wrong_hidden
    assert source is not None and source.shape==hidden.shape,'BOT_BAD_SOURCE'
    vector=model.head(source.float())
    if condition=='zero':vector=vector*0.
    if track_vector and vector.requires_grad:vector.retain_grad()
    prefix=fixed_consumer_ids(model,tok,device)
    suffix,targets=joint.target_tokens(tok,row,'answer')
    assert prefix.size(1)+2+len(suffix)+len(targets)<=max_seq,'BOT_SEQUENCE_TOO_LONG'
    trailing=torch.tensor([[model.end_id]+suffix+targets[:-1]],device=device,dtype=torch.long)
    emb=model.backbone.get_input_embeddings()
    sequence=torch.cat((emb(prefix),vector[:,None,:].to(emb.weight.dtype),emb(trailing)),dim=1)
    state=model.backbone.get_decoder()(inputs_embeds=sequence,
      attention_mask=torch.ones((1,sequence.size(1)),dtype=torch.long,device=device),
      use_cache=False,return_dict=True).last_hidden_state
    first=prefix.size(1)+1+1+len(suffix)-1
    positions=torch.arange(first,first+len(targets),device=device)
    output=model.backbone.get_output_embeddings()
    logits=output(state[0].index_select(0,positions).to(output.weight.dtype)).float()
    labels=torch.tensor(targets,device=device,dtype=torch.long)
    ce=F.cross_entropy(logits,labels,reduction='mean')
    assert bool(torch.isfinite(ce)),'BOT_CE_NONFINITE'
    return ce,vector


def group_norm(named):
    """Aggregate gradient statistics on-device; make ONE host transfer per group.

    The previous implementation synchronized CUDA separately for every
    tensor's isfinite check and norm conversion, twice per training step.
    """
    grads=[p.grad.detach() for _,p in named if p.grad is not None]
    if not grads:
        return {'norm':0.0,'tensors_with_grad':0,'nonzero_tensors':0}
    device=grads[0].device
    assert all(g.device==device for g in grads),'BOT_GRADIENT_DEVICE_MISMATCH'
    sizes=torch.stack([torch.linalg.vector_norm(g.float()) for g in grads])
    # All statistics stay on the originating device until the final .tolist().
    stats=torch.stack((torch.linalg.vector_norm(sizes),
                       (sizes>0).sum().float(),torch.isfinite(sizes).all().float()))
    norm,nonzero,finite=stats.detach().cpu().tolist()
    assert finite==1.0,'BOT_NONFINITE_GRADIENT'
    return {'norm':float(norm),'tensors_with_grad':len(grads),
            'nonzero_tensors':int(nonzero)}


def cuda_preflight(model,device):
    """Refuse offloaded trainables and report the actual model/device/dtypes.

    This is metadata only: it never transfers tensors or changes model state.
    GPU memory is reported from the active PyTorch allocator.
    """
    assert device in ('cuda','cpu'),'BOT_DEVICE'
    if device=='cpu':
        assert all(p.device.type=='cpu' for p in model.parameters()),'BOT_CPU_REQUEST_HAS_CUDA_PARAMETERS'
        return {'status':'CPU','device':'cpu'}
    if not torch.cuda.is_available():
        raise RuntimeError('BOT_CUDA_REQUESTED_BUT_UNAVAILABLE')
    gpu=torch.cuda.current_device()
    expected=torch.device('cuda',gpu)
    params=list(model.parameters())
    lora=zero.lora_named_parameters(model.backbone)
    head=list(model.head.parameters())
    assert params and lora and head,'BOT_CUDA_MODEL_INCOMPLETE'
    assert all(p.device==expected for p in params),'BOT_CUDA_PARAMETER_OFFLOAD'
    assert all(p.requires_grad and p.dtype==torch.float32 for _,p in lora),'BOT_CUDA_LORA_NOT_FP32'
    assert all(p.requires_grad and p.dtype==torch.float32 for p in head),'BOT_CUDA_HEAD_NOT_FP32'
    assert all(p.device==expected for p in model.backbone.get_input_embeddings().parameters()),'BOT_CUDA_EMBEDDING_OFFLOAD'
    emb=model.backbone.get_input_embeddings().weight
    assert emb.dtype in (torch.float16,torch.bfloat16),'BOT_CUDA_BACKBONE_NOT_MIXED_PRECISION'
    total=torch.cuda.get_device_properties(gpu).total_memory
    return {'status':'CUDA_READY','device':str(expected),
       'gpu_name':torch.cuda.get_device_name(gpu),'total_memory_mib':round(total/1048576,1),
       'backbone_embedding_dtype':str(emb.dtype).replace('torch.',''),
       'head_dtype':'float32','lora_dtype':'float32',
       'lora_parameter_tensors':len(lora),
       'allocated_mib':round(torch.cuda.memory_allocated(gpu)/1048576,1),
       'reserved_mib':round(torch.cuda.memory_reserved(gpu)/1048576,1)}


def cuda_memory(device):
    """Read allocator counters without a CUDA->CPU tensor synchronization."""
    if device!='cuda':return None
    return {'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/1048576,1),
            'allocated_mib':round(torch.cuda.memory_allocated()/1048576,1),
            'reserved_mib':round(torch.cuda.memory_reserved()/1048576,1)}


def gradient_probe(model,tok,rows,device,max_seq):
    """Measure answer-only derivative, separately for latent, head and LoRA.

    This test does not infer useful learning merely from nonzero gradients.
    It rejects a broken/detached gradient path before expending training.
    """
    model.eval()
    lora,head=joint.param_scope(model)
    model.zero_grad(set_to_none=True)
    hidden=hidden_from_question(model,tok,rows[0],device)
    ce,vector=consumer_ce(model,tok,rows[0],hidden,max_seq=max_seq,track_vector=True)
    ce.backward()
    latent_norm=float(vector.grad.detach().float().norm()) if vector.grad is not None else 0.
    report={'schema':SCHEMA,'loss':float(ce.detach()),'latent_grad_norm':latent_norm,
      'head':group_norm(head),'lora':group_norm(lora),'producer_source_detached':not hidden.requires_grad,
      'base_parameters_frozen':all(not p.requires_grad for n,p in model.backbone.named_parameters()
                              if not n.endswith(('.lora_a','.lora_b'))),
      'latent_grad_device':str(vector.grad.device) if vector.grad is not None else None,
      'cuda_memory':cuda_memory(device)}
    report['pass']=(latent_norm>0 and report['head']['norm']>0 and report['lora']['norm']>0
                    and report['producer_source_detached'] and report['base_parameters_frozen'])
    model.zero_grad(set_to_none=True)
    return report


def pick_other(rows,index):
    # No label oracle is used to select an unrelated inference vector.
    assert len(rows)>1,'BOT_NEEDS_MISMATCH'
    return rows[(index+2)%len(rows)] if len(rows)>2 else rows[(index+1)%len(rows)]


def train_one(model,tok,row,other,device,max_seq,rank_weight,*,restore=False,replay_weight=0.7):
    hidden=hidden_from_question(model,tok,row,device)
    wrong=hidden_from_question(model,tok,other,device)
    correct,_=consumer_ce(model,tok,row,hidden,max_seq=max_seq)
    # Controls are no-grad reference targets. The primary answer CE on the
    # intact vector carries gradients all the way through HybridLink.
    with torch.no_grad():
        neg,_=consumer_ce(model,tok,row,hidden,condition='shuffled',wrong_hidden=wrong,max_seq=max_seq)
        z,_=consumer_ce(model,tok,row,hidden,condition='zero',max_seq=max_seq)
    rank=(F.softplus(0.3+correct-neg)+F.softplus(0.3+correct-z))/2
    loss=correct+rank_weight*rank
    regular=None
    if restore:
        # Restore question-visible operation but protect the established
        # question-hidden channel against being discarded through replay.
        visible=joint.second_pass_logprob(model,tok,row,hidden,max_seq=max_seq)['loss']
        regular=visible
        loss=visible+replay_weight*loss
    return loss,{'channel_ce':float(correct.detach()),'ranking':float(rank.detach()),
                 'normal_ce':float(regular.detach()) if regular is not None else None,
                 'wrong_ce':float(neg.detach()),'zero_ce':float(z.detach())}


def channel_prefix(model,tok,row,condition,*,wrong=None):
    """Separate producer and consumer KV caches; never append the question cache."""
    assert condition in ('intact','zero','shuffled','marker_only'),'BOT_CHANNEL_MODE'
    device=next(model.head.parameters()).device
    ids=fixed_consumer_ids(model,tok,device)
    if condition=='marker_only':
        tail=ids.new_tensor([[model.end_id]+joint.suffix_ids(tok,'answer')])
        full=torch.cat((ids,tail),dim=1);mask=torch.ones_like(full)
        _,logits,cache=model._step(input_ids=full,mask=mask)
        return logits,cache,mask
    hidden=hidden_from_question(model,tok,row,device)
    if condition=='shuffled':
        assert wrong is not None,'BOT_SHUFFLED_MISSING'
        hidden=hidden_from_question(model,tok,wrong,device)
    vec=model.head(hidden.float())
    if condition=='zero':vec=torch.zeros_like(vec)
    # Fixed consumer prefix contains no operand tokens; cached producer
    # history is deliberately discarded before beginning consumption.
    mask=torch.ones_like(ids)
    _,_,cache=model._step(input_ids=ids,mask=mask)
    mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
    _,_,cache=model._step(inputs_embeds=vec[:,None,:],mask=mask,cache=cache)
    tail=ids.new_tensor([[model.end_id]+joint.suffix_ids(tok,'answer')])
    mask=torch.cat((mask,mask.new_ones((1,tail.size(1)))),dim=1)
    _,logits,cache=model._step(input_ids=tail,mask=mask,cache=cache)
    return logits,cache,mask


def decode_channel(model,tok,row,condition,*,wrong=None,budget=24):
    assert 1<=budget<=100,'BOT_BUDGET'
    with torch.inference_mode():
        logits,cache,mask=channel_prefix(model,tok,row,condition,wrong=wrong)
        generated=[]
        for _ in range(budget):
            token=int(logits.argmax(dim=-1).item())
            if tok.eos_token_id is not None and token==tok.eos_token_id:break
            generated.append(token)
            mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
            _,logits,cache=model._step(input_ids=mask.new_tensor([[token]]),mask=mask,cache=cache)
        completion=tok.decode(generated,skip_special_tokens=True)
        matched=re.match(r'^\s*([+-]?\d+)(?!\d)',completion)
        value=int(matched.group(1)) if matched else None
        return {'answer':value,'correct':value==row['answer'],
                'parseable':value is not None,'completion':completion[:160]}


def evaluate(model,tok,rows,*,scope,budget=24):
    model.eval()
    results=[]
    for i,row in enumerate(rows):
        other=pick_other(rows,i)
        conditions=('intact','zero','shuffled','marker_only') if scope=='channel' else ('intact','zero','shuffled','marker_only','text')
        variants={}
        for condition in conditions:
            if scope=='channel':
                variants[condition]=decode_channel(model,tok,row,condition,wrong=other,budget=budget)
            else:
                variants[condition]=joint.decode(model,tok,row,condition,negative=other,budget=budget)
        record={'id':row['id'],'expected':row['answer'],'family':row['family'],'variants':variants}
        results.append(record)
    scores={name:sum(int(r['variants'][name]['correct']) for r in results) for name in conditions}
    return {'cases':len(rows),'scores':scores,'records':results,'scope':scope}


def channel_advantage(scores,margin=2):
    return scores['intact']>=scores['zero']+margin and scores['intact']>=scores['shuffled']+margin


def _save_state(path,model,optimizer,step,best,config_hash,device):
    zero.atomic_torch(path,{'schema':SCHEMA,'step':step,'best':best,'config_hash':config_hash,
       'state':joint._state(model),'optimizer':optimizer.state_dict(),
       'torch_rng':torch.get_rng_state(),
       'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None})


def _make_config(sources,data,*,seed,train_count,dev_count,qualify_count,test_count,
          channel_steps,restore_steps,eval_every,max_seq,head_lr,lora_lr,rank_weight,replay_weight,budget):
    return {'schema':SCHEMA,'sources':sources,'dataset_fingerprint':zero.fingerprint(data),
      'seed':seed,'train_count':train_count,'dev_count':dev_count,
      'qualify_count':qualify_count,'test_count':test_count,
      'channel_steps':channel_steps,'restore_steps':restore_steps,'eval_every':eval_every,
      'max_seq':max_seq,'head_lr':head_lr,'lora_lr':lora_lr,'rank_weight':rank_weight,
      'replay_weight':replay_weight,'budget':budget,'mode':'question-hidden-consumer;detached-producer;self-generated-latent',
      'gold_answer_input_forbidden':True,'blind_test_once':True}


def run(*,stage='all',device='cuda',base=interface.hybrid.DEFAULT_OUTPUT,
        numerical=zero.DEFAULT_OUTPUT,interface_dir=interface.ROOT,
        repair_root=joint.align.ROOT,joint_root=joint.ROOT,output=ROOT,
        seed=2041,train_count=96,dev_count=18,qualify_count=18,test_count=18,
        channel_steps=480,restore_steps=160,eval_every=20,max_seq=192,
        head_lr=3e-5,lora_lr=5e-6,rank_weight=0.7,replay_weight=0.7,budget=24,
        resume=False,stop_after=None,loader=None,allow_download=False):
    stages=('gradient','channel','qualify','restore','gate')
    assert stage in ('all',)+stages,'BOT_UNKNOWN_STAGE'
    assert device in ('cuda','cpu'),'BOT_DEVICE'
    assert 1<=channel_steps<=10000 and 1<=restore_steps<=10000 and 1<=eval_every,'BOT_STEPS'
    assert 0<head_lr<0.01 and 0<lora_lr<0.01 and 0<=rank_weight<=5 and 0<=replay_weight<=5,'BOT_RATES'
    assert 48<=max_seq<=2048 and 1<=budget<=100,'BOT_SEQUENCE_BUDGET'
    paths=list(map(Path,(base,numerical,interface_dir,repair_root,joint_root,output)))
    base,numerical,interface_dir,repair_root,joint_root,output=paths
    source_guard(output,base,numerical,interface_dir,repair_root,joint_root)
    sources=source_provenance(interface_dir,repair_root,joint_root)
    data=data_splits(seed,train_count,dev_count,qualify_count,test_count)
    cfg=_make_config(sources,data,seed=seed,train_count=train_count,dev_count=dev_count,
         qualify_count=qualify_count,test_count=test_count,channel_steps=channel_steps,
         restore_steps=restore_steps,eval_every=eval_every,max_seq=max_seq,
         head_lr=head_lr,lora_lr=lora_lr,rank_weight=rank_weight,replay_weight=replay_weight,budget=budget)
    config_path=output/'config.json'
    if config_path.exists():
        assert joint.align._json(config_path)==cfg,'BOT_CONFIG_DRIFT'
    else:
        assert not output.exists() or not any(output.iterdir()),'BOT_OUTPUT_NOT_FRESH'
        output.mkdir(parents=True,exist_ok=True);zero.atomic_json(config_path,cfg)
    cfg_hash=zero.fingerprint(cfg)
    model,tok=load_trainable(device,base,numerical,interface_dir,repair_root,joint_root,
                             loader=loader,allow_download=allow_download)
    placement=cuda_preflight(model,device)
    if device=='cuda':
        torch.cuda.reset_peak_memory_stats()
        print('HYBRID_BOT_CUDA_PREFLIGHT='+json.dumps(placement,sort_keys=True),flush=True)
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed)
    initial_base=zero.frozen_backbone_sha256(model.backbone)

    def ensure_sources():
        assert source_provenance(interface_dir,repair_root,joint_root)==sources,'BOT_SOURCE_CHANGED'
        assert zero.frozen_backbone_sha256(model.backbone)==initial_base,'BOT_BASE_MUTATED'

    def do_gradient():
        probe=output/'gradient.json'
        if probe.exists():
            r=joint.align._json(probe)
            assert r['schema']==SCHEMA and r['pass'] is True,'BOT_GRADIENT_FAILED'
            return r
        r=gradient_probe(model,tok,data['train'],device,max_seq)
        zero.atomic_json(probe,r)
        print('HYBRID_BOT_GRADIENT='+json.dumps(r,sort_keys=True),flush=True)
        assert r['pass'],'BOT_GRADIENT_FAILED'
        return r

    def trained(which):
        folder=output/which
        cp=folder/'best.pt'
        result=folder/'result.json'
        assert cp.is_file() and result.is_file(),'BOT_STAGE_INCOMPLETE_'+which
        record=joint.align._json(result)
        assert record['status']=='TRAINED_UNCERTIFIED' and record['checkpoint_sha256']==interface.fingerprint(cp),'BOT_STATE_CHANGED_'+which
        checkpoint=torch.load(cp,map_location='cpu',weights_only=True)
        assert checkpoint['schema']==SCHEMA and checkpoint['config_hash']==cfg_hash,'BOT_CONFIG_CHECKPOINT_DRIFT'
        joint._restore_state(model,checkpoint['state'])
        return record

    def training(which):
        assert which in ('channel','restore')
        if which=='channel':do_gradient()
        else:
            qualify=joint.align._json(output/'qualify.json')
            assert qualify['status']=='CHANNEL_PASS','BOT_CHANNEL_NOT_QUALIFIED'
            trained('channel')
        folder=output/which
        steps=channel_steps if which=='channel' else restore_steps
        folder.mkdir(parents=True,exist_ok=True)
        last=folder/'last.pt'
        opt=torch.optim.AdamW([{'params':[p for p in model.head.parameters() if p.requires_grad],'lr':head_lr},
           {'params':[p for _,p in zero.lora_named_parameters(model.backbone)],'lr':lora_lr}],weight_decay=0)
        best=float('inf');start=0
        if last.exists():
            assert resume,'BOT_RESUME_REQUIRED_'+which
            cp=torch.load(last,map_location='cpu',weights_only=True)
            assert cp['schema']==SCHEMA and cp['config_hash']==cfg_hash and cp['step']<steps,'BOT_RESUME_INVALID'
            joint._restore_state(model,cp['state']);opt.load_state_dict(cp['optimizer'])
            torch.set_rng_state(cp['torch_rng'])
            if device=='cuda':torch.cuda.set_rng_state_all(cp['cuda_rng'])
            start=int(cp['step']);best=float(cp['best'])
        else:
            assert not (folder/'best.pt').exists(),'BOT_ALREADY_TRAINED_'+which
        last_metrics={}
        for step in range(start+1,steps+1):
            i=(step-1)%train_count
            row=data['train'][i];other=pick_other(data['train'],i)
            model.eval();opt.zero_grad(set_to_none=True)
            loss,metrics=train_one(model,tok,row,other,device,max_seq,rank_weight,
                                  restore=which=='restore',replay_weight=replay_weight)
            loss.backward()
            g_head=group_norm(joint.param_scope(model)[1]);g_lora=group_norm(joint.param_scope(model)[0])
            assert g_head['norm']>0 and g_lora['norm']>0,'BOT_GRADIENT_VANISHED'
            torch.nn.utils.clip_grad_norm_([p for p in model.head.parameters() if p.requires_grad]+
                      [p for _,p in zero.lora_named_parameters(model.backbone)],1.)
            opt.step()
            if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
                model.eval()
                with torch.no_grad():
                    ces=[]
                    for x in data['dev']:
                        h=hidden_from_question(model,tok,x,device)
                        v,_=consumer_ce(model,tok,x,h,max_seq=max_seq)
                        ces.append(float(v))
                dev=sum(ces)/len(ces)
                normal_dev=None
                if which=='restore':
                    with torch.no_grad():
                        normals=[]
                        for x in data['dev']:
                            h=hidden_from_question(model,tok,x,device)
                            normals.append(float(joint.second_pass_logprob(
                                model,tok,x,h,max_seq=max_seq)['loss']))
                    normal_dev=sum(normals)/len(normals)
                # Protect the channel while selecting restoration checkpoints
                # that also improve question-visible answer loss.
                objective=dev if which=='channel' else normal_dev+replay_weight*dev
                if objective<best:
                    best=objective
                    zero.atomic_torch(folder/'best.pt',{'schema':SCHEMA,'step':step,'state':joint._state(model),
                            'config_hash':cfg_hash,'dev_channel_ce':dev,'dev_normal_ce':normal_dev,'selection_objective':objective})
                last_metrics={'step':step,'total':steps,'dev_channel_ce':dev,'best_channel_ce':best,
                    'dev_normal_ce':normal_dev,'checkpoint_objective':objective,
                    'gradient_head':g_head['norm'],'gradient_lora':g_lora['norm'],**metrics}
                if device=='cuda':last_metrics['cuda_memory']=cuda_memory(device)
                _save_state(last,model,opt,step,best,cfg_hash,device)
                print('HYBRID_BOT_'+which.upper()+'_STEP='+json.dumps(last_metrics,sort_keys=True),flush=True)
            if stop_after and step>=stop_after:break
        status='TRAINED_UNCERTIFIED' if step==steps else 'PAUSED_RESUMABLE'
        result={'schema':SCHEMA,'status':status,'step':step,'steps':steps,'best_checkpoint_objective':best,
           'checkpoint_sha256':interface.fingerprint(folder/'best.pt'), 'last':last_metrics}
        zero.atomic_json(folder/'result.json',result)
        ensure_sources()
        print('HYBRID_BOT_'+which.upper()+'_RESULT='+json.dumps(result,sort_keys=True),flush=True)
        return result

    def qualify():
        assert not (output/'qualify.json').exists(),'BOT_QUALIFICATION_ALREADY_USED'
        trained('channel')
        # Qualifier is separate from final blind set; positive result authorizes
        # only controlled question-visible restoration, never deeper recurrence.
        scores=evaluate(model,tok,data['qualify'],scope='channel',budget=budget)
        passed=channel_advantage(scores['scores']) and scores['scores']['intact']>=int(0.5*len(data['qualify']))
        record={'schema':SCHEMA,'status':'CHANNEL_PASS' if passed else 'CHANNEL_FAIL',
            'deeper_training_permitted':False,'restore_permitted':passed,
            'scores':scores['scores'],'cases':scores['cases'],
            'split_fingerprint':zero.fingerprint(data['qualify']),
            'records':scores['records']}
        zero.atomic_json(output/'qualify.json',record)
        print('HYBRID_BOT_QUALIFY='+json.dumps({k:v for k,v in record.items() if k!='records'},sort_keys=True),flush=True)
        return record

    def final_gate():
        assert joint.align._json(output/'qualify.json')['status']=='CHANNEL_PASS','BOT_CHANNEL_NOT_QUALIFIED'
        assert not (output/'gate_started.json').exists(),'BOT_FINAL_GATE_ALREADY_USED'
        trained('restore')
        zero.atomic_json(output/'gate_started.json',{'schema':SCHEMA,
          'split_fingerprint':zero.fingerprint(data['test']),
          'checkpoint':interface.fingerprint(output/'restore'/'best.pt')})
        normal=evaluate(model,tok,data['test'],scope='normal',budget=budget)
        channel=evaluate(model,tok,data['test'],scope='channel',budget=budget)
        n=normal['scores'];c=channel['scores']
        passed=channel_advantage(c) and channel_advantage(n) and n['intact']>=n['text']-1
        result={'schema':SCHEMA,'status':'ONE_STEP_PASS' if passed else 'ONE_STEP_FAIL',
           'deeper_training_permitted':bool(passed),'causal_advantage_pass':bool(passed),
           'normal_scores':n,'hidden_channel_scores':c,'cases':len(data['test']),
           'question_visible_text_matched_model':True,'gold_answer_used_as_input':False,
           'oracle_latents_at_inference':0,'records':{'normal':normal['records'],'channel':channel['records']}}
        zero.atomic_json(output/'gate_result.json',result)
        ensure_sources()
        print('HYBRID_BOT_GATE_RESULT='+json.dumps({k:v for k,v in result.items() if k!='records'},sort_keys=True),flush=True)
        return result

    if stage=='gradient':r=do_gradient()
    elif stage=='channel':r=training('channel')
    elif stage=='qualify':r=qualify()
    elif stage=='restore':r=training('restore')
    elif stage=='gate':r=final_gate()
    else:
        do_gradient()
        # Respect explicit resume semantics; earlier complete phases cannot
        # silently be overwritten or rerun using a different checkpoint.
        if (output/'channel'/'result.json').exists() and joint.align._json(output/'channel'/'result.json')['status']=='TRAINED_UNCERTIFIED':
            trained('channel')
        else:
            r=training('channel')
            if r['status']!='TRAINED_UNCERTIFIED':return r
        if (output/'qualify.json').exists():
            q=joint.align._json(output/'qualify.json')
        else:q=qualify()
        if q['status']!='CHANNEL_PASS':return q
        if (output/'restore'/'result.json').exists() and joint.align._json(output/'restore'/'result.json')['status']=='TRAINED_UNCERTIFIED':
            trained('restore')
        else:
            r=training('restore')
            if r['status']!='TRAINED_UNCERTIFIED':return r
        r=final_gate()
    return r


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','gradient','channel','qualify','restore','gate'),default='all')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--base',type=Path,default=interface.hybrid.DEFAULT_OUTPUT)
    p.add_argument('--numerical',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--interface-dir',type=Path,default=interface.ROOT)
    p.add_argument('--repair-root',type=Path,default=joint.align.ROOT)
    p.add_argument('--joint-root',type=Path,default=joint.ROOT)
    p.add_argument('--output',type=Path,default=ROOT)
    for arg,default in [('seed',2041),('train-count',96),('dev-count',18),('qualify-count',18),
      ('test-count',18),('channel-steps',480),('restore-steps',160),('eval-every',20),('max-seq',192),
      ('budget',24)]:p.add_argument('--'+arg,type=int,default=default)
    for arg,default in [('head-lr',3e-5),('lora-lr',5e-6),('rank-weight',0.7),('replay-weight',0.7)]:
        p.add_argument('--'+arg,type=float,default=default)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int)
    p.add_argument('--allow-model-download',action='store_true')
    a=vars(p.parse_args(argv));a['allow_download']=a.pop('allow_model_download')
    try:
        result=run(**a)
        print('HYBRID_BOT_RESULT='+json.dumps({k:v for k,v in result.items() if k!='records'},sort_keys=True),flush=True)
        return 0
    except (AssertionError,ValueError,RuntimeError,OSError,FileNotFoundError) as e:
        print('HYBRID_BOT_REFUSED='+type(e).__name__+':'+str(e),file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':sys.exit(main())
