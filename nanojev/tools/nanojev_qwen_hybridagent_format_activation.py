#!/usr/bin/env python3
"""Bounded HybridSFT *format activation*: teacher-forced text cross-entropy only.

Paper-aligned stage semantics: the entire text trajectory including <latent>,
latent content, </latent> and answer is supervised as TEXT; no HybridLink
regression and no self-conditioned replacement occur. For this 6GB adaptation
ONLY attention LoRA parameters are trained; original Qwen and HybridLink are
immutable. This is not a claim of complete full-backbone reproduction.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

import torch
from torch.nn import functional as F

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybridagent_activation as activation

SCHEMA = 'nanojev.qwen3-06b.hybrid-format-activation-lora.v1'
OUTPUT = Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_format_activation_v1'
BASELINE = hybrid.DEFAULT_OUTPUT
BACKBONE = zero.DEFAULT_OUTPUT


def encode_format(tokenizer, model, row, max_seq=192):
    """Explicitly supervise BOTH mode markers, unlike latent regression phase.

    Prompt is never labeled. An answer appears only in training labels, not
    in the diagnostic prompt. Zero masking leakage is asserted explicitly.
    """
    prompt = f"Question: Compute {row['expression']}.\nReasoning: "
    thought = ('Equal and opposite terms cancel. ' +
               ('Combine the signed remainders.' if row['family']=='composed' else
                'Keep the signed remainder.'))
    after = '\nAnswer: '+str(row['answer'])+'\n'
    p=tokenizer(prompt,add_special_tokens=False)['input_ids']
    t=tokenizer(thought,add_special_tokens=False)['input_ids']
    e=tokenizer(after,add_special_tokens=False)['input_ids']
    assert p and t and e, 'FORMAT_EMPTY_SEGMENT'
    sequence=p+[model.start_id]+t+[model.end_id]+e
    assert len(sequence)<=max_seq,'FORMAT_OVER_MAX_SEQ'
    # Every assistant token in hybrid trajectory is CE supervised in format activation.
    labels=[-100]*len(p)+sequence[len(p):]
    assert labels[len(p)]==model.start_id and labels[len(p)+1+len(t)]==model.end_id
    assert all(x==-100 for x in labels[:len(p)])
    assert all(x!=-100 for x in labels[len(p):]), 'FORMAT_TEXT_TARGET_MASK_GAP'
    return {'ids':sequence,'labels':labels,'entry':len(p),'exit':len(p)+len(t)+1,
            'prompt':p, 'answer':row['answer']}


def format_loss(model, data, device):
    """Sparse CE at labeled target positions, avoiding full [length, vocab] logits."""
    ids=torch.tensor([data['ids']],dtype=torch.long,device=device)
    labels=torch.tensor(data['labels'][1:],dtype=torch.long,device=device)
    # The input tokens are all teacher-forced TEXT, including latent content.
    decoder=model.backbone.get_decoder()
    embed=model.backbone.get_input_embeddings()
    output=model.backbone.get_output_embeddings()
    states=decoder(inputs_embeds=embed(ids),attention_mask=torch.ones_like(ids),
                   return_dict=True,use_cache=False).last_hidden_state
    idx=torch.nonzero(labels>=0).reshape(-1)
    assert idx.numel()>=4,'FORMAT_NO_SUPERVISED_TEXT'
    logits=output(states[0].index_select(0,idx).to(dtype=output.weight.dtype)).float()
    true=labels.index_select(0,idx)
    losses=F.cross_entropy(logits,true,reduction='none')
    # Emphasize marker transitions, while all other text tokens remain supervised.
    weights=torch.ones_like(losses)
    weights[(true==model.start_id)|(true==model.end_id)]=2.0
    weighted=(losses*weights).sum()/weights.sum()
    assert torch.isfinite(weighted).all(),'FORMAT_NONFINITE_LOSS'
    return {'loss':weighted,'marker_ce':losses[(true==model.start_id)|(true==model.end_id)].mean(),
            'text_ce':losses.mean(), 'token_count':int(idx.numel())}


def eval_format(model,tokenizer,rows,device,max_seq):
    old=model.backbone.training
    model.backbone.eval()
    records=[]
    try:
        with torch.no_grad():
            for row in rows:
                data=encode_format(tokenizer,model,row,max_seq)
                vals=format_loss(model,data,device)
                records.append({k:float(vals[k].detach()) for k in ('loss','marker_ce','text_ce')})
    finally:
        model.backbone.train(old)
    return {k:sum(x[k] for x in records)/len(records) for k in records[0]}


def marker_probabilities(model,tokenizer,rows,device,max_seq):
    """Teacher-forced token probabilities at the two mode boundaries only.

    These measure *format readiness*, not autonomously chosen mode switching.
    """
    model.backbone.eval()
    values={'entry':[], 'exit':[]}
    with torch.inference_mode():
        for row in rows:
            ex=encode_format(tokenizer,model,row,max_seq)
            ids=torch.tensor([ex['ids']],device=device,dtype=torch.long)
            states=model.backbone.get_decoder()(inputs_embeds=model.backbone.get_input_embeddings()(ids),
                attention_mask=torch.ones_like(ids),use_cache=False,return_dict=True).last_hidden_state
            for name,pos,token in [('entry',ex['entry'],model.start_id),('exit',ex['exit'],model.end_id)]:
                logits=model.backbone.get_output_embeddings()(states[0,pos-1:pos].to(
                    dtype=model.backbone.get_output_embeddings().weight.dtype)).float()[0]
                values[name].append(float(torch.softmax(logits,dim=-1)[token]))
    return {name+'_probability':sum(v)/len(v) for name,v in values.items()}


def source_hashes(baseline,backbone):
    names={'adapter':baseline/'adapter.pt','adapter_config':baseline/'adapter_config.json',
           'lora':backbone/'best_lora.pt','lora_config':backbone/'run_config.json',
           'lora_result':backbone/'result.json'}
    for k,p in names.items():assert p.is_file(),'FORMAT_SOURCE_MISSING:'+k
    return {k:activation.file_hash(p) for k,p in names.items()}


def assert_provenance(baseline,backbone,hashes):
    c=json.loads((backbone/'run_config.json').read_text(encoding='utf8'))
    r=json.loads((backbone/'result.json').read_text(encoding='utf8'))
    assert c['schema']==zero.SCHEMA and r['status']=='TRAINING_SMOKE_COMPLETED_UNCERTIFIED', 'FORMAT_BACKBONE_INCOMPLETE'
    assert Path(c['baseline']).resolve()==baseline.resolve(),'FORMAT_BASELINE_DIR_DRIFT'
    assert c['base_hashes']['adapter.pt']==hashes['adapter'],'FORMAT_BASE_ADAPTER_DRIFT'
    assert c['base_hashes']['adapter_config.json']==hashes['adapter_config'],'FORMAT_BASE_CONFIG_DRIFT'
    assert c['base_model']==hybrid.MODEL_ID and c['revision']==hybrid.MODEL_REVISION, 'FORMAT_MODEL_REVISION_DRIFT'
    return c


def _rng_state(device):
    return {'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all() if str(device).startswith('cuda') else None}


def train(*,device='cuda',baseline=BASELINE,backbone=BACKBONE,output=OUTPUT,
          steps=24,eval_every=6,seed=71,max_seq=192,lr=7e-6,
          train_count=96,dev_count=18,test_count=18,stop_after=None,resume=False,
          allow_download=False,loader=None):
    baseline,backbone,output=map(Path,(baseline,backbone,output))
    assert 1<=steps<=2000 and 1<=eval_every<=steps and lr>0 and max_seq>=64,'FORMAT_INVALID_CONFIG'
    assert all(n>=6 and n%6==0 for n in (train_count,dev_count,test_count)),'FORMAT_UNBALANCED_SPLIT'
    if stop_after is not None: assert 1<=stop_after<=steps,'FORMAT_BAD_STOP_STEP'
    for src in (baseline,backbone):
        assert output.resolve()!=src.resolve() and output.resolve() not in src.resolve().parents and src.resolve() not in output.resolve().parents,'FORMAT_OUTPUT_OVERLAPS_SOURCE'
    hashes=source_hashes(baseline,backbone)
    prev=assert_provenance(baseline,backbone,hashes)
    cases=zero.make_cases(seed,train_count,dev_count,test_count)
    load=loader or hybrid.load_qwen
    model,tokenizer,details=load(device=device,allow_download=allow_download,
         precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'auto')
    hybrid.load_adapter(baseline,model,details)
    assert details['base_model']==hybrid.MODEL_ID and details['revision']==hybrid.MODEL_REVISION,'FORMAT_QWEN_BASE_DRIFT'
    model.backbone.requires_grad_(False);model.head.requires_grad_(False)
    modules=zero.install_lora(model.backbone,rank=int(prev['rank']),alpha=float(prev['alpha']),
             required_layers=int(model.backbone.config.num_hidden_layers))
    pt=torch.load(backbone/'best_lora.pt',map_location='cpu',weights_only=True)
    assert pt['schema']==zero.SCHEMA,'FORMAT_SOURCE_LORA_SCHEMA'
    zero.restore_lora(model.backbone,pt['lora'])
    torch.manual_seed(seed)
    if device=='cuda':torch.cuda.manual_seed_all(seed);torch.cuda.reset_peak_memory_stats()
    opt=torch.optim.AdamW([p for _,p in zero.lora_named_parameters(model.backbone)],lr=lr,weight_decay=0.0)
    config={'schema':SCHEMA,'baseline':str(baseline.resolve()),'backbone':str(backbone.resolve()),
            'sources':hashes,'base_model':details['base_model'],'revision':details['revision'],
            'steps':steps,'eval_every':eval_every,'seed':seed,'max_seq':max_seq,'lr':lr,
            'train_count':train_count,'dev_count':dev_count,'test_count':test_count,
            'data_hash':zero.fingerprint(cases),'modules':list(modules),
            'stage':'format_activation','head_trainable':False,
            'stage_semantics':'all_text_tokens_supervised_including_mode_markers;no_latent_replacement',
            'deviation':'frozen_base_low_rank_attention_only_6GB'}
    sha=zero.fingerprint(config)
    recover_prestep=False
    if not resume and output.exists() and any(output.iterdir()):
        entries={p.name for p in output.iterdir()}
        assert entries=={'run_config.json'},'FORMAT_REFUSES_EXISTING_OUTPUT'
        saved=json.loads((output/'run_config.json').read_text(encoding='utf8'))
        assert saved==config,'FORMAT_PRESTEP_CONFIG_DRIFT'
        recover_prestep=True
    if resume:
        assert (output/'run_config.json').is_file() and (output/'last.pt').is_file(),'FORMAT_RESUME_FILES_MISSING'
        assert json.loads((output/'run_config.json').read_text(encoding='utf8'))==config,'FORMAT_RESUME_CONFIG_DRIFT'
        cp=torch.load(output/'last.pt',map_location='cpu',weights_only=True)
        assert cp['schema']==SCHEMA and cp['config_hash']==sha,'FORMAT_RESUME_CHECKPOINT_DRIFT'
        zero.restore_lora(model.backbone,cp['lora']);opt.load_state_dict(cp['optimizer'])
        torch.set_rng_state(cp['torch_rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(cp['cuda_rng'])
        start=int(cp['step']);best=float(cp['best_loss']);initial=cp['initial_dev']
        assert 0<start<steps,'FORMAT_RESUME_STEP_INVALID'
    else:
        if not recover_prestep:
            output.mkdir(parents=True,exist_ok=True)
            zero.atomic_json(output/'run_config.json',config)
        else:
            print('HYBRID_FORMAT_RECOVERED_PRESTEP='+str(output),flush=True)
        start=0;best=float('inf')
        initial=eval_format(model,tokenizer,cases['dev'][:6],device,max_seq)
        initial['transitions']=marker_probabilities(model,tokenizer,cases['dev'][:6],device,max_seq)
    frozen_before=zero.frozen_backbone_sha256(model.backbone)
    head_before={k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}
    lora_before=zero.lora_state(model.backbone)
    last=None
    for step in range(start+1,steps+1):
        row=cases['train'][(step-1)%len(cases['train'])]
        data=encode_format(tokenizer,model,row,max_seq)
        model.backbone.train();opt.zero_grad(set_to_none=True)
        # LoRA is FP32, forward weights may be BF16. BF16 autocast leaves CE in FP32.
        with torch.autocast(device_type='cuda' if device=='cuda' else 'cpu',
                            enabled=device=='cuda',dtype=torch.bfloat16):
            terms=format_loss(model,data,device)
        terms['loss'].backward()
        report=zero.gradient_report(model.backbone)
        assert not report['missing'],'FORMAT_GRADIENT_MISSING:'+str(report['missing'][:4])
        assert not report['nonfinite'],'FORMAT_GRADIENT_NONFINITE:'+str(report['nonfinite'][:4])
        assert report['nonzero'],'FORMAT_GRADIENT_ZERO'
        assert all(p.grad is None for n,p in model.backbone.named_parameters()
                   if not n.endswith(('.lora_a','.lora_b'))),'FORMAT_FROZEN_GRADIENT'
        assert all(p.grad is None for p in model.head.parameters()),'FORMAT_HEAD_GRADIENT'
        torch.nn.utils.clip_grad_norm_([p for _,p in zero.lora_named_parameters(model.backbone)],1.)
        opt.step()
        if step==1 or step%eval_every==0 or step==steps or (stop_after and step>=stop_after):
            last=eval_format(model,tokenizer,cases['dev'][:6],device,max_seq)
            last['transitions']=marker_probabilities(model,tokenizer,cases['dev'][:6],device,max_seq)
            if last['loss']<best:
                best=last['loss']
                zero.atomic_torch(output/'best_format_lora.pt',{'schema':SCHEMA,'config_hash':sha,'step':step,
                              'lora':zero.lora_state(model.backbone),'dev':last})
            cp={'schema':SCHEMA,'config_hash':sha,'step':step,'lora':zero.lora_state(model.backbone),
                'optimizer':opt.state_dict(),'initial_dev':initial,'best_loss':best,**_rng_state(device)}
            zero.atomic_torch(output/'last.pt',cp)
            print('HYBRID_FORMAT_STEP='+json.dumps({'step':step,'total':steps,
                  'train_loss':float(terms['loss'].detach()),'dev_loss':last['loss'],
                  'dev_marker_ce':last['marker_ce'],'entry_probability':last['transitions']['entry_probability'],
                  'exit_probability':last['transitions']['exit_probability'],
                  'best_dev_loss':best},sort_keys=True),flush=True)
        if stop_after and step>=stop_after:break
    assert frozen_before==zero.frozen_backbone_sha256(model.backbone),'FORMAT_FROZEN_WEIGHT_MUTATION'
    assert all(torch.equal(v,model.head.state_dict()[k].detach().cpu()) for k,v in head_before.items()),'FORMAT_HEAD_MUTATION'
    assert any(not torch.equal(v,zero.lora_state(model.backbone)[k]) for k,v in lora_before.items()),'FORMAT_LORA_NOT_UPDATED'
    assert source_hashes(baseline,backbone)==hashes,'FORMAT_SOURCE_MUTATION'
    finished=(step==steps)
    result={'schema':SCHEMA,'status':'FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED' if finished else 'PAUSED_RESUMABLE',
       'step':step,'steps':steps,'initial_dev':initial,'current_dev':last,'best_dev_loss':best,
       'head_changed':False,'lora_changed':True,'frozen_preserved':True,'source_mutations':0,
       'trainable_component':'attention_LoRA_only','format_only':True,
       'cuda_peak_allocated_mb':round(torch.cuda.max_memory_allocated()/1048576,2) if device=='cuda' else None,
       'next':'paired_diagnostic_before_any_joint_retraining'}
    zero.atomic_json(output/'result.json',result)
    print('HYBRID_FORMAT_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--baseline',type=Path,default=BASELINE)
    p.add_argument('--backbone',type=Path,default=BACKBONE)
    p.add_argument('--output',type=Path,default=OUTPUT)
    p.add_argument('--steps',type=int,default=24)
    p.add_argument('--eval-every',type=int,default=6)
    p.add_argument('--seed',type=int,default=71)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--lr',type=float,default=7e-6)
    p.add_argument('--train-count',type=int,default=96)
    p.add_argument('--dev-count',type=int,default=18)
    p.add_argument('--test-count',type=int,default=18)
    p.add_argument('--stop-after',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        report=train(device=args.device,baseline=args.baseline,backbone=args.backbone,
                     output=args.output,steps=args.steps,eval_every=args.eval_every,
                     seed=args.seed,max_seq=args.max_seq,lr=args.lr,train_count=args.train_count,
                     dev_count=args.dev_count,test_count=args.test_count,
                     stop_after=args.stop_after,resume=args.resume,allow_download=args.allow_model_download)
        return 0 if report['status'] in ('PAUSED_RESUMABLE','FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED') else 2
    except (RuntimeError,AssertionError,ValueError,FileExistsError,OSError) as exc:
        print('HYBRID_FORMAT_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':sys.exit(main())
