#!/usr/bin/env python3
"""Versioned HybridAgent marker repair -> aligned head -> one-step gate.

No old run is modified. The training job is bounded and resumable; inference
controls never receive a gold answer or teacher latent. The one-step gate is
reported independently from the optimizer/supervision mechanics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_hybridagent_format_activation as fmt
from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
from tools import nanojev_qwen_zero_line_backbone as zero

SCHEMA = 'nanojev.qwen3-06b.hybrid-interface-repair.v1'
ROOT = Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_interface_repair_v1'


def fingerprint(path):
    return activation.file_hash(Path(path))


def source_files(base, numerical, head):
    paths = {'adapter':base/'adapter.pt','adapter_config':base/'adapter_config.json',
             'numerical_lora':numerical/'best_lora.pt','numerical_config':numerical/'run_config.json',
             'numerical_result':numerical/'result.json',
             'initial_head':head/'best_head.pt','initial_head_config':head/'run_config.json',
             'initial_head_result':head/'result.json'}
    for key,p in paths.items():
        assert p.is_file(),'INTERFACE_SOURCE_MISSING:'+key
    fmt.assert_provenance(base,numerical,fmt.source_hashes(base,numerical))
    hc=json.loads((head/'run_config.json').read_text(encoding='utf8'))
    hr=json.loads((head/'result.json').read_text(encoding='utf8'))
    assert hc['schema']==activation.SCHEMA and hr['status']=='PASS_MECHANICS_ONLY','INTERFACE_HEAD_SOURCE_NOT_COMPLETE'
    assert Path(hc['baseline_dir']).resolve()==base.resolve(),'INTERFACE_HEAD_BASELINE_MISMATCH'
    assert hc['baseline_adapter_hash']==fingerprint(base/'adapter.pt'),'INTERFACE_HEAD_ADAPTER_CHANGED'
    return {name:fingerprint(p) for name,p in paths.items()}


class MarkerRows(nn.Module):
    """Two trainable INPUT and OUTPUT rows; all other Qwen vocab rows immutable.

    Independent parameters prevent a dense trainable 151k x 1024 embedding.
    No whole embedding weight is promoted into an optimizer parameter group.
    """
    def __init__(self,model):
        super().__init__()
        self.ids=(int(model.start_id),int(model.end_id))
        embeddings=model.backbone.get_input_embeddings().weight.detach()
        output=model.backbone.get_output_embeddings().weight.detach()
        self.input_rows=nn.Parameter(embeddings[list(self.ids)].clone().float())
        self.output_rows=nn.Parameter(output[list(self.ids)].clone().float())

    def input_override(self,embeddings,ids):
        assert ids.ndim==2 and ids.shape[0]==1 and embeddings.shape[:2]==ids.shape
        positions=torch.nonzero((ids[0]==self.ids[0])|(ids[0]==self.ids[1])).flatten()
        if positions.numel()==0:return embeddings
        which=(ids[0].index_select(0,positions)==self.ids[1]).long()
        new_rows=self.input_rows.index_select(0,which).to(dtype=embeddings.dtype)
        return embeddings.index_copy(1,positions,new_rows[None])

    def output_override(self,logits,hidden):
        # Base frozen lm_head gives scores for EVERY other token; only replace
        # the special-token scores with a gradient-bearing 2-row projection.
        # Autocast may produce BF16 projection scores despite FP32 inputs.
        # index_copy requires exact dtype agreement on CUDA; the conversion
        # remains differentiable back to the two trainable marker rows.
        base_logits=logits.float()
        scores=F.linear(hidden.float(),self.output_rows).to(dtype=base_logits.dtype)
        indices=torch.tensor(self.ids,device=logits.device,dtype=torch.long)
        return base_logits.index_copy(-1,indices,scores)

    def apply_to_model(self,model):
        """Install distinct trained marker input/output rows in an IN-MEMORY model.

        Qwen3-0.6B-Base ties input embeddings to lm_head. Writing both trained
        rows into that shared parameter is impossible: the output rows would
        overwrite the input rows. Instead intercept only the two marker LOGITS
        at inference, preserving tying for all other 151k vocabulary entries.
        The FP32 output rows stay separate and no checkpoint is modified.
        """
        assert getattr(model, '_hybrid_marker_output_hook', None) is None, (
            'INTERFACE_MARKER_OUTPUT_OVERRIDE_ALREADY_INSTALLED')
        with torch.no_grad():
            ids=list(self.ids)
            ie=model.backbone.get_input_embeddings().weight
            output_layer=model.backbone.get_output_embeddings()
            assert output_layer is not None, 'INTERFACE_OUTPUT_PROJECTION_MISSING'
            oe=output_layer.weight
            tied=oe.data_ptr()==ie.data_ptr()
            ie[ids]=self.input_rows.detach().to(ie)
            if not tied:
                oe[ids]=self.output_rows.detach().to(oe)
        if tied:
            assert self.output_rows.device==ie.device, 'INTERFACE_OUTPUT_ROWS_DEVICE_MISMATCH'

            def _trained_marker_logits(_module, inputs, logits):
                assert len(inputs)==1, 'INTERFACE_OUTPUT_PROJECTION_INPUT_DRIFT'
                return self.output_override(logits,inputs[0])

            # register_forward_hook is invoked by the real lm_head during every
            # Qwen causal forward, including cached recurrent latent steps.
            model._hybrid_marker_output_hook=output_layer.register_forward_hook(
                _trained_marker_logits)
        model._hybrid_marker_tied_output=bool(tied)

    def cpu_state(self):
        return {k:v.detach().cpu().clone() for k,v in self.state_dict().items()}


def marker_format_loss(model,rows,example,device,marker_weight=16.0):
    ids=torch.tensor([example['ids']],dtype=torch.long,device=device)
    labels=torch.tensor(example['labels'][1:],dtype=torch.long,device=device)
    emb=model.backbone.get_input_embeddings()(ids)
    emb=rows.input_override(emb,ids)
    states=model.backbone.get_decoder()(inputs_embeds=emb,attention_mask=torch.ones_like(ids),
                    use_cache=False,return_dict=True).last_hidden_state
    pos=torch.nonzero(labels>=0).flatten()
    targets=labels.index_select(0,pos)
    out=model.backbone.get_output_embeddings()
    h=states[0].index_select(0,pos)
    logits=rows.output_override(out(h.to(out.weight.dtype)),h)
    ce=F.cross_entropy(logits,targets,reduction='none')
    marker_mask=(targets==model.start_id)|(targets==model.end_id)
    assert int(marker_mask.sum())==2,'INTERFACE_BOTH_MARKERS_REQUIRED'
    weights=torch.where(marker_mask,ce.new_full(ce.shape,marker_weight),torch.ones_like(ce))
    loss=(ce*weights).sum()/weights.sum()
    assert torch.isfinite(loss),'INTERFACE_NONFINITE_FORMAT_LOSS'
    with torch.no_grad():
        probs=logits.softmax(-1)
        desired=probs.gather(1,targets[:,None])[:,0]
    return {'loss':loss,'entry_prob':desired[targets==model.start_id].mean().item(),
            'exit_prob':desired[targets==model.end_id].mean().item(),
            'marker_ce':ce[marker_mask].mean().item(),'text_ce':ce[~marker_mask].mean().item()}


def mean_format(model,markers,tokenizer,items,device,max_seq,marker_weight):
    model.backbone.eval()
    with torch.no_grad():
        data=[marker_format_loss(model,markers,fmt.encode_format(tokenizer,model,r,max_seq),device,
                                 marker_weight) for r in items]
    return {k:sum(float(x[k]) for x in data)/len(data) for k in data[0]}


def _states():
    return {'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}


def _model(device,base,numerical,*,loader=None,allow_download=False):
    load=loader or hybrid.load_qwen
    model,tok,details=load(device=device,allow_download=allow_download,
        precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'auto')
    hybrid.load_adapter(base,model,details)
    assert details['base_model']==hybrid.MODEL_ID and details['revision']==hybrid.MODEL_REVISION,'INTERFACE_BASE_REVISION_DRIFT'
    model.backbone.requires_grad_(False);model.head.requires_grad_(False)
    nc=json.loads((numerical/'run_config.json').read_text(encoding='utf8'))
    zero.install_lora(model.backbone,int(nc['rank']),float(nc['alpha']),
                      required_layers=int(model.backbone.config.num_hidden_layers))
    return model,tok,details


def _hash_config(obj):
    return zero.fingerprint(obj)


def _source_verify(base,numerical,head,expected):
    assert source_files(base,numerical,head)==expected,'INTERFACE_SOURCES_CHANGED'


def train_format(*,device='cuda',base=hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
                 initial_head=activation.DEFAULT_RUN,output=ROOT,steps=240,eval_every=20,
                 seed=71,train_count=96,dev_count=18,test_count=18,max_seq=192,
                 lr=7e-6,marker_lr=0.003,marker_weight=16.0,
                 stop_after=None,resume=False,loader=None,allow_download=False):
    base,numerical,initial_head,output=map(Path,(base,numerical,initial_head,output))
    assert 1<=steps<=2000 and 1<=eval_every<=steps and lr>0 and marker_lr>0 and marker_weight>1,'INTERFACE_BAD_HYPERPARAMETERS'
    assert all(x>=6 and x%6==0 for x in (train_count,dev_count,test_count)),'INTERFACE_DATA_COUNT_UNBALANCED'
    assert stop_after is None or 1<=stop_after<=steps,'INTERFACE_BAD_STOP_AFTER'
    for path in (base,numerical,initial_head):
        assert output.resolve()!=path.resolve() and path.resolve() not in output.resolve().parents and output.resolve() not in path.resolve().parents,'INTERFACE_OUTPUT_OVERLAP'
    expected=source_files(base,numerical,initial_head)
    data=zero.make_cases(seed,train_count,dev_count,test_count)
    cfg={'schema':SCHEMA,'stage':'marker_format','base':str(base.resolve()),
         'numerical':str(numerical.resolve()),'head':str(initial_head.resolve()),
         'sources':expected,'data_fingerprint':zero.fingerprint(data),
         'steps':steps,'eval_every':eval_every,'seed':seed,'train_count':train_count,
         'dev_count':dev_count,'test_count':test_count,'max_seq':max_seq,'lr':lr,
         'marker_lr':marker_lr,'marker_weight':marker_weight,'model':hybrid.MODEL_ID,
         'revision':hybrid.MODEL_REVISION}
    config_hash=_hash_config(cfg)
    target=output/'format'
    if target.exists() and any(target.iterdir()):
        assert (target/'config.json').is_file(),'INTERFACE_FORMAT_OUTPUT_EXISTS'
        assert json.loads((target/'config.json').read_text(encoding='utf8'))==cfg,'INTERFACE_FORMAT_CONFIG_DRIFT'
        assert resume or set(p.name for p in target.iterdir())=={'config.json'},'INTERFACE_FORMAT_OUTPUT_EXISTS'
    else:
        target.mkdir(parents=True,exist_ok=True)
        zero.atomic_json(target/'config.json',cfg)
    model,tok,_=_model(device,base,numerical,loader=loader,allow_download=allow_download)
    initial=torch.load(numerical/'best_lora.pt',map_location='cpu',weights_only=True)
    assert initial['schema']==zero.SCHEMA,'INTERFACE_NUMERICAL_LORA_SCHEMA'
    zero.restore_lora(model.backbone,initial['lora'])
    markers=MarkerRows(model).to(device=device)
    parameters=[{'params':[p for _,p in zero.lora_named_parameters(model.backbone)],'lr':lr},
                {'params':list(markers.parameters()),'lr':marker_lr}]
    opt=torch.optim.AdamW(parameters,weight_decay=0.)
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed);torch.cuda.reset_peak_memory_stats()
    if resume:
        assert (target/'last.pt').is_file(),'INTERFACE_FORMAT_RESUME_MISSING'
        checkpoint=torch.load(target/'last.pt',map_location='cpu',weights_only=True)
        assert checkpoint['schema']==SCHEMA and checkpoint['config_hash']==config_hash,'INTERFACE_FORMAT_RESUME_DRIFT'
        zero.restore_lora(model.backbone,checkpoint['lora'])
        markers.load_state_dict(checkpoint['markers'],strict=True)
        opt.load_state_dict(checkpoint['optimizer'])
        torch.set_rng_state(checkpoint['torch_rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(checkpoint['cuda_rng'])
        start=int(checkpoint['step']);best=float(checkpoint['best_dev_loss']);initial_dev=checkpoint['initial_dev']
        assert 0<start<steps,'INTERFACE_FORMAT_RESUME_COMPLETE'
    else:
        start=0;best=float('inf')
        initial_dev=mean_format(model,markers,tok,data['dev'][:6],device,max_seq,marker_weight)
    frozen_hash=zero.frozen_backbone_sha256(model.backbone)
    head_initial={k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}
    with torch.enable_grad():
        for step in range(start+1,steps+1):
            row=data['train'][(step-1)%len(data['train'])]
            example=fmt.encode_format(tok,model,row,max_seq)
            model.backbone.train();opt.zero_grad(set_to_none=True)
            with torch.autocast('cuda' if device=='cuda' else 'cpu',enabled=device=='cuda',dtype=torch.bfloat16):
                terms=marker_format_loss(model,markers,example,device,marker_weight)
            terms['loss'].backward()
            report=zero.gradient_report(model.backbone)
            assert not report['missing'] and not report['nonfinite'] and report['nonzero'],'INTERFACE_LORA_GRADIENT_INVALID:'+str(report)
            for name,p in markers.named_parameters():
                assert p.grad is not None and torch.isfinite(p.grad).all() and bool(p.grad.abs().sum()>0), 'INTERFACE_MARKER_GRADIENT_INVALID:'+name
            torch.nn.utils.clip_grad_norm_([p for group in opt.param_groups for p in group['params']],1.)
            opt.step()
            if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
                dev=mean_format(model,markers,tok,data['dev'][:6],device,max_seq,marker_weight)
                if dev['loss']<best:
                    best=dev['loss']
                    zero.atomic_torch(target/'best.pt',{'schema':SCHEMA,'config_hash':config_hash,'step':step,
                         'lora':zero.lora_state(model.backbone),'markers':markers.cpu_state(),'dev':dev})
                snapshot={'schema':SCHEMA,'config_hash':config_hash,'step':step,
                  'lora':zero.lora_state(model.backbone),'markers':markers.cpu_state(),
                  'optimizer':opt.state_dict(),'initial_dev':initial_dev,'best_dev_loss':best,**_states()}
                zero.atomic_torch(target/'last.pt',snapshot)
                print('HYBRID_INTERFACE_FORMAT_STEP='+json.dumps({'step':step,'total':steps,'train_loss':float(terms['loss'].detach()),
                    'dev':dev,'best_dev_loss':best},sort_keys=True),flush=True)
            if stop_after and step>=stop_after:break
    assert frozen_hash==zero.frozen_backbone_sha256(model.backbone),'INTERFACE_FROZEN_BACKBONE_MUTATED'
    assert all(torch.equal(head_initial[k],v.detach().cpu()) for k,v in model.head.state_dict().items()),'INTERFACE_HEAD_MUTATED'
    _source_verify(base,numerical,initial_head,expected)
    finished=(step==steps)
    report={'schema':SCHEMA,'status':'FORMAT_TRAINED_UNCERTIFIED' if finished else 'PAUSED_RESUMABLE',
            'step':step,'steps':steps,'initial_dev':initial_dev,'last_dev':dev,'best_loss':best,
            'source_mutations':0,'frozen_base_intact':True,'head_unchanged':True,
            'gpu_peak_mb':round(torch.cuda.max_memory_allocated()/1048576,2) if device=='cuda' else None}
    zero.atomic_json(target/'result.json',report)
    print('HYBRID_INTERFACE_FORMAT_RESULT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def _load_format(*,device,base,numerical,output,loader=None,allow_download=False):
    path=output/'format'; cfg=json.loads((path/'config.json').read_text(encoding='utf8'))
    result=json.loads((path/'result.json').read_text(encoding='utf8'))
    assert cfg['schema']==SCHEMA and result['status']=='FORMAT_TRAINED_UNCERTIFIED','INTERFACE_FORMAT_NOT_COMPLETE'
    assert Path(cfg['base']).resolve()==base.resolve() and Path(cfg['numerical']).resolve()==numerical.resolve(),'INTERFACE_FORMAT_PATH_DRIFT'
    assert source_files(base,numerical,Path(cfg['head']))==cfg['sources'],'INTERFACE_FORMAT_SOURCE_CHANGED'
    cp=torch.load(path/'best.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==SCHEMA and cp['config_hash']==_hash_config(cfg),'INTERFACE_FORMAT_CHECKPOINT_DRIFT'
    model,tok,_=_model(device,base,numerical,loader=loader,allow_download=allow_download)
    zero.restore_lora(model.backbone,cp['lora'])
    markers=MarkerRows(model).to(device=device)
    markers.load_state_dict(cp['markers'],strict=True)
    markers.apply_to_model(model)
    model.backbone.eval()
    return model,tok,markers,cfg,cp


def _pairs(model,tok,items,device,max_seq):
    xs,ys,exits=[],[],[]
    model.backbone.eval()
    with torch.no_grad():
        for row in items:
            d=fmt.encode_format(tok,model,row,max_seq)
            tokens=torch.tensor([d['ids']],device=device)
            embs=model.backbone.get_input_embeddings()(tokens)
            h=model.backbone.get_decoder()(inputs_embeds=embs,attention_mask=torch.ones_like(tokens),
                use_cache=False,return_dict=True).last_hidden_state[0]
            sources=torch.arange(d['entry'],d['exit'],device=device)
            xs.append(h.index_select(0,sources).float().cpu())
            ys.append(embs[0].index_select(0,sources+1).float().cpu())
            exits.extend([i==d['exit']-1 for i in range(d['entry'],d['exit'])])
    return torch.cat(xs),torch.cat(ys),torch.tensor(exits,dtype=torch.bool)


def realign(*,device='cuda',base=hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
            output=ROOT,steps=120,eval_every=20,lr=5e-5,max_seq=192,
            stop_after=None,resume=False,loader=None,allow_download=False):
    base,numerical,output=map(Path,(base,numerical,output))
    assert 1<=steps<=3000 and 1<=eval_every<=steps and lr>0,'INTERFACE_ALIGNMENT_HYPERPARAMETERS'
    model,tok,_,fc,fmt_best=_load_format(device=device,base=base,numerical=numerical,output=output,loader=loader,allow_download=allow_download)
    prior=Path(fc['head']);cp=torch.load(prior/'best_head.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==activation.SCHEMA,'INTERFACE_PRIOR_HEAD_SCHEMA'
    model.head.to(device=device,dtype=torch.float32)
    model.head.load_state_dict(cp['head'],strict=True)
    model.head.requires_grad_(True)
    frozen_hash=zero.frozen_backbone_sha256(model.backbone)
    data=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])
    # Frozen Qwen: teacher context is never presented in the one-step free-running gate.
    train_cache=_pairs(model,tok,data['train'],device,max_seq)
    dev_cache=_pairs(model,tok,data['dev'][:6],device,max_seq)
    align_cfg={'schema':SCHEMA,'stage':'realignment','format_checkpoint_sha256':fingerprint(output/'format'/'best.pt'),
          'initial_head_sha256':fingerprint(prior/'best_head.pt'),
          'base':str(base.resolve()),'numerical':str(numerical.resolve()),'steps':steps,
          'eval_every':eval_every,'lr':lr,'max_seq':max_seq,'data_fingerprint':zero.fingerprint(data)}
    c_hash=_hash_config(align_cfg)
    target=output/'align'
    if target.exists() and any(target.iterdir()):
        assert (target/'config.json').is_file() and json.loads((target/'config.json').read_text(encoding='utf8'))==align_cfg,'INTERFACE_ALIGNMENT_CONFIG_DRIFT'
        assert resume or {p.name for p in target.iterdir()}=={'config.json'},'INTERFACE_ALIGNMENT_OUTPUT_EXISTS'
    else:
        target.mkdir(parents=True,exist_ok=True);zero.atomic_json(target/'config.json',align_cfg)
    # Realignment is head-only: all Qwen and numerical LoRA weights are immutable.
    model.backbone.requires_grad_(False)
    lora_before=zero.lora_state(model.backbone)
    opt=torch.optim.AdamW(model.head.parameters(),lr=lr,weight_decay=0.)
    if resume:
        assert (target/'last.pt').is_file(),'INTERFACE_ALIGNMENT_RESUME_MISSING'
        last=torch.load(target/'last.pt',map_location='cpu',weights_only=True)
        assert last['schema']==SCHEMA and last['config_hash']==c_hash,'INTERFACE_ALIGNMENT_RESUME_DRIFT'
        model.head.load_state_dict(last['head'],strict=True)
        opt.load_state_dict(last['optimizer']);torch.set_rng_state(last['torch_rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(last['cuda_rng'])
        start=int(last['step']);best=float(last['best_dev_loss']);initial=last['initial_dev']
        assert 0<start<steps,'INTERFACE_ALIGNMENT_RESUME_COMPLETE'
    else:
        start=0;best=float('inf')
        initial=activation.evaluate(model.head,dev_cache,device)
    x,y,exit_mask=train_cache
    for step in range(start+1,steps+1):
        idx=torch.arange((step-1)*32,(step-1)*32+32)%len(x)
        a,b,c=x[idx].to(device),y[idx].to(device),exit_mask[idx].to(device)
        model.head.train();opt.zero_grad(set_to_none=True)
        terms=activation.loss_components(model.head,a,b,c)
        terms['loss'].backward()
        grads=[p.grad for p in model.head.parameters()]
        assert all(g is not None and torch.isfinite(g).all() for g in grads) and any(g.abs().sum()>0 for g in grads),'INTERFACE_HEAD_GRADIENT_INVALID'
        torch.nn.utils.clip_grad_norm_(model.head.parameters(),1.)
        opt.step()
        if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
            dev=activation.evaluate(model.head,dev_cache,device)
            if dev['loss']<best:
                best=dev['loss']
                zero.atomic_torch(target/'best.pt',{'schema':SCHEMA,'config_hash':c_hash,'step':step,
                    'head':{k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()},'dev':dev})
            zero.atomic_torch(target/'last.pt',{'schema':SCHEMA,'config_hash':c_hash,'step':step,
                'head':{k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()},
                'optimizer':opt.state_dict(),'initial_dev':initial,'best_dev_loss':best,**_states()})
            print('HYBRID_INTERFACE_ALIGNMENT_STEP='+json.dumps({'step':step,'total':steps,
                 'dev':dev,'train_loss':float(terms['loss'].detach()),'best_dev_loss':best},sort_keys=True),flush=True)
        if stop_after and step>=stop_after:break
    assert frozen_hash==zero.frozen_backbone_sha256(model.backbone),'INTERFACE_ALIGNMENT_BACKBONE_MUTATED'
    assert all(torch.equal(v,zero.lora_state(model.backbone)[k]) for k,v in lora_before.items()),'INTERFACE_ALIGNMENT_LORA_MUTATED'
    completed=step==steps
    result={'schema':SCHEMA,'status':'HEAD_REALIGNED_UNCERTIFIED' if completed else 'PAUSED_RESUMABLE',
            'step':step,'initial_dev':initial,'last_dev':dev,'best_dev_loss':best,
            'backbone_mutations':0}
    zero.atomic_json(target/'result.json',result)
    print('HYBRID_INTERFACE_ALIGNMENT_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def gate(*,device='cuda',base=hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
         output=ROOT,n=6,tokens=48,loader=None,allow_download=False,
         min_entry_probability=1e-4,min_exit_probability=1e-4):
    base,numerical,output=map(Path,(base,numerical,output))
    model,tok,markers,fc,fp=_load_format(device=device,base=base,numerical=numerical,output=output,
                                    loader=loader,allow_download=allow_download)
    ac=json.loads((output/'align'/'config.json').read_text(encoding='utf8'))
    ar=json.loads((output/'align'/'result.json').read_text(encoding='utf8'))
    assert ac['schema']==SCHEMA and ar['status']=='HEAD_REALIGNED_UNCERTIFIED','INTERFACE_HEAD_NOT_REALIGNED'
    assert ac['format_checkpoint_sha256']==fingerprint(output/'format'/'best.pt'),'INTERFACE_FORMAT_HEAD_DRIFT'
    cp=torch.load(output/'align'/'best.pt',map_location='cpu',weights_only=True)
    assert cp['schema']==SCHEMA and cp['config_hash']==_hash_config(ac),'INTERFACE_ALIGNED_HEAD_DRIFT'
    model.head.load_state_dict(cp['head'],strict=True)
    data=zero.make_cases(fc['seed'],fc['train_count'],fc['dev_count'],fc['test_count'])
    rows=data['test'][:n]
    assert n>=6 and n%6==0 and n<=len(data['test']),'INTERFACE_GATE_BAD_CASE_COUNT'
    # Evaluate the SAVED FP32 output rows. Reconstructing MarkerRows(model) loses
    # them on tied embeddings (the input weights now occupy both tied tables).
    metrics=mean_format(model,markers,tok,data['dev'][:6],device,fc['max_seq'],fc['marker_weight'])
    for key in ('entry_prob','exit_prob','loss','marker_ce'):
        assert math.isclose(metrics[key],fp['dev'][key],rel_tol=0.001,abs_tol=0.001), (
            'INTERFACE_MARKER_CHECKPOINT_ROUNDTRIP_DRIFT:' + key +
            f': saved={fp["dev"][key]} reconstructed={metrics[key]}')
    # Also demand that the REAL backbone forward (the inference path) emits
    # the independently trained output-marker logits, even with tied weights.
    with torch.no_grad():
        ids=torch.tensor([fmt.encode_format(tok,model,data['dev'][0],fc['max_seq'])['ids']],
                         dtype=torch.long,device=device)
        real=model.backbone(input_ids=ids,use_cache=False,output_hidden_states=True,
                            return_dict=True)
        final=real.hidden_states[-1]
        expected=F.linear(final.float(),markers.output_rows.float())
        # Untied output projections can have a bias (e.g. the CPU test model);
        # tied Qwen3 output projection is biasless and uses the FP32 hook.
        output_layer=model.backbone.get_output_embeddings()
        if not getattr(model,'_hybrid_marker_tied_output',False) and output_layer.bias is not None:
            expected=expected+output_layer.bias[list(markers.ids)].float()
        actual=real.logits.index_select(-1,torch.tensor(markers.ids,dtype=torch.long,device=device))
        assert torch.allclose(actual.float(),expected.float(),rtol=0.02,atol=0.1), (
            'INTERFACE_INFERENCE_MARKER_LOGIT_ROUNDTRIP_DRIFT')
    record=[]
    with torch.inference_mode():
        for row in rows:
            ids=torch.tensor([tok(f"Question: Compute {row['expression']}.\nReasoning: ",add_special_tokens=False)['input_ids']],
                             dtype=torch.long,device=device)
            variants={}
            for depth,control in ((0,'text'),(1,'intact'),(1,'zero_latent')):
                result=paired.decode_once(model,tok,ids,depth,tokens,corrupt=(control=='zero_latent'))
                variants[control]={'answer':result['answer_label'],'leading_integer':result['leading_integer'],
                                   'completion':result['completion'],'seconds':result['seconds'],'tokens':result['tokens']}
            record.append({'id':row['id'],'expected':row['answer'],'sign':row['sign'],
                           'family':row['family'],'variants':variants})
    def score(k):return sum(x['variants'][k]['answer']==x['expected'] for x in record)
    def parse(k):return sum(x['variants'][k]['answer'] is not None for x in record)
    text,intact,corrupt=score('text'),score('intact'),score('zero_latent')
    entry=float(metrics['entry_prob']);leave=float(metrics['exit_prob'])
    markers_good=entry>=min_entry_probability and leave>=min_exit_probability
    # Require at least one correctly parsed intact case, >= corrupted control,
    # and no >1-case regression vs text-only, at fixed 1-step depth.
    latent_good=(intact>=1 and intact>=corrupt and intact>=text-1 and parse('intact')>=max(1,parse('text')-1))
    result={'schema':SCHEMA,'status':'ONE_STEP_GATE_PASS' if markers_good and latent_good else 'ONE_STEP_GATE_FAIL',
          'format_checkpoint_sha256':fingerprint(output/'format'/'best.pt'),
          'aligned_head_sha256':fingerprint(output/'align'/'best.pt'),
          'entry_probability':entry,'exit_probability':leave,
          'marker_checkpoint_roundtrip':True,
          'tied_output_marker_override':bool(getattr(model,'_hybrid_marker_tied_output',False)),
          'required_entry_probability':min_entry_probability,
          'required_exit_probability':min_exit_probability,'markers_pass':markers_good,'latent_pass':latent_good,
          'correct_text':text,'correct_intact':intact,'correct_zero_latent':corrupt,
          'parseable_text':parse('text'),'parseable_intact':parse('intact'),
          'cases':len(record),'oracle_latents_supplied':0,'records':record,
          'deeper_training_permitted':bool(markers_good and latent_good)}
    # Preserve the first failed gate report (without this fix) for comparison.
    historical=output/'gate_pre_tied_output.json'
    previous=output/'gate_result.json'
    if previous.is_file() and not historical.exists():
        historical.write_bytes(previous.read_bytes())
    zero.atomic_json(previous,result)
    print('HYBRID_INTERFACE_GATE_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','format','align','gate'),default='all')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--baseline',type=Path,default=hybrid.DEFAULT_OUTPUT)
    p.add_argument('--numerical',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--initial-head',type=Path,default=activation.DEFAULT_RUN)
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--format-steps',type=int,default=240)
    p.add_argument('--align-steps',type=int,default=120)
    p.add_argument('--eval-every',type=int,default=20)
    p.add_argument('--marker-lr',type=float,default=0.003)
    p.add_argument('--min-marker-probability',type=float,default=1e-4)
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--resume',action='store_true',help='Resume selected interrupted training stage')
    p.add_argument('--stop-after',type=int)
    args=p.parse_args(argv)
    try:
        assert 0<args.min_marker_probability<1,'INTERFACE_INVALID_MARKER_GATE'
        if args.stage in ('all','format'):
            r=train_format(device=args.device,base=args.baseline,numerical=args.numerical,
                initial_head=args.initial_head,output=args.output,steps=args.format_steps,
                eval_every=args.eval_every,marker_lr=args.marker_lr,stop_after=args.stop_after,
                resume=args.resume,allow_download=args.allow_model_download)
            if r['status']=='PAUSED_RESUMABLE' or args.stage=='format':return 0
            best_format=torch.load(args.output/'format'/'best.pt',map_location='cpu',weights_only=True)
            transition=best_format['dev']
            passed=(transition['entry_prob']>=args.min_marker_probability and
                    transition['exit_prob']>=args.min_marker_probability)
            print('HYBRID_INTERFACE_MARKER_GATE='+json.dumps({'pass':passed,
                'entry_probability':transition['entry_prob'],
                'exit_probability':transition['exit_prob'],
                'threshold':args.min_marker_probability},sort_keys=True),flush=True)
            if not passed:return 3  # Do NOT consume GPU aligning an unlearned protocol.
        if args.stage in ('all','align'):
            r=realign(device=args.device,base=args.baseline,numerical=args.numerical,
                output=args.output,steps=args.align_steps,eval_every=args.eval_every,
                stop_after=args.stop_after,resume=args.resume if args.stage=='align' else False,
                allow_download=args.allow_model_download)
            if r['status']=='PAUSED_RESUMABLE' or args.stage=='align':return 0
        report=gate(device=args.device,base=args.baseline,numerical=args.numerical,
                    output=args.output,allow_download=args.allow_model_download,
                    min_entry_probability=args.min_marker_probability,
                    min_exit_probability=args.min_marker_probability)
        return 0 if report['status']=='ONE_STEP_GATE_PASS' else 3
    except (AssertionError,RuntimeError,ValueError,FileNotFoundError,KeyError,OSError) as exc:
        print('HYBRID_INTERFACE_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':sys.exit(main())
