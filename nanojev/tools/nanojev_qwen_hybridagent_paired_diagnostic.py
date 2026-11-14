#!/usr/bin/env python3
"""Read-only matched text-vs-latent evaluation of HybridSFT phase checkpoints.

Compare: numerical-LoRA+activation head, format-LoRA+activation head,
previous self-conditioned joint best (when available). Evaluate text-only and
1/2/4 latent steps, plus zeroed latent-vector intervention. No gold answers
or teacher latents appear in model prompts or forward inputs.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys
import time

import torch
from torch import nn

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_hybridagent_activation as activation
from tools import nanojev_qwen_hybridagent_self_conditioned as joint
from tools import nanojev_qwen_hybridagent_format_activation as format_train

SCHEMA='nanojev.qwen3-06b.hybrid-paired-text-latent.v1'
OUTPUT=Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_paired_diagnostic_v1'
JOINT=joint.OUTPUT


def selected_cases(seed=71,test_count=18,n=6):
    rows=zero.make_cases(seed,96,18,test_count)['test']
    assert 6<=n<=len(rows) and n%6==0,'PAIR_NEEDS_BALANCED_CASE_COUNT'
    sel=rows[:n]
    assert all(sum(r['sign']==sg for r in sel)==n//3 for sg in ('zero','positive','negative')),'PAIR_SIGN_IMBALANCE'
    assert all(sum(r['family']==fm for r in sel)==n//2 for fm in ('direct','composed')),'PAIR_FAMILY_IMBALANCE'
    return sel


def parse_answer(text):
    """Two independent views: strict explicit Answer and opening bare integer.

    Never infer a correct answer from incidental digits anywhere in a story.
    """
    formal=re.search(r'(?m)^\s*Answer:\s*([+-]?\d+)\b',text)
    first=re.match(r'^\s*([+-]?\d+)\s*(?:\n|$)',text)
    return {'answer_label':int(formal.group(1)) if formal else None,
            'leading_integer':int(first.group(1)) if first else None}


class ZeroLatent(nn.Module):
    def __init__(self,original):
        super().__init__()
        self.original=original
    def forward(self,x):
        return torch.zeros_like(self.original(x))


def decode_once(model,tokenizer,ids,depth,budget,*,corrupt=False):
    """No answer labels used; preserve the same Qwen KV cache across steps."""
    assert 0<=depth<=32 and 1<=budget<=256,'PAIR_INVALID_LENGTH'
    model.backbone.eval();model.head.eval()
    original_head=model.head
    if ids.device.type=='cuda':torch.cuda.synchronize(ids.device)
    start=time.perf_counter()
    if corrupt:model.head=ZeroLatent(original_head)
    try:
        with torch.inference_mode():
            if depth:
                _,_,cache,mask,trace=model.rollout(ids,depth,return_trace=True)
                generated=model.text_after_latents(cache,mask,max_tokens=budget,eos_token_id=tokenizer.eos_token_id)
            else:
                _,logits,cache,mask,_=model.rollout(ids,0)
                generated=[]
                for _ in range(budget):
                    tok=logits.argmax(dim=-1,keepdim=True)
                    tid=int(tok.item())
                    if tokenizer.eos_token_id is not None and tid==tokenizer.eos_token_id:break
                    generated.append(tid)
                    mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
                    _,logits,cache=model._step(input_ids=tok,cache=cache,mask=mask)
            text=tokenizer.decode(generated,skip_special_tokens=True)
    finally:
        if corrupt:model.head=original_head
    if ids.device.type=='cuda':torch.cuda.synchronize(ids.device)
    return {'completion':text[:300],'tokens':len(generated),'latents':depth,
            'seconds':time.perf_counter()-start,**parse_answer(text)}


def paired_scores(records):
    groups={}
    for r in records:
        key=(r['configuration'],r['depth'],r['control'])
        groups.setdefault(key,[]).append(r)
    scores=[]
    for (config,depth,control),xs in sorted(groups.items()):
        n=len(xs)
        scores.append({'configuration':config,'depth':depth,'control':control,'cases':n,
         'answer_label_correct':sum(r['answer_label']==r['expected'] for r in xs),
         'leading_integer_correct':sum(r['leading_integer']==r['expected'] for r in xs),
         'parseable_answer_label':sum(r['answer_label'] is not None for r in xs),
         'average_text_tokens':sum(r['tokens'] for r in xs)/n,
         'average_latent_steps':sum(r['latents'] for r in xs)/n,
         'average_wall_seconds':sum(r['seconds'] for r in xs)/n,
         'negative_correct':sum(r['answer_label']==r['expected'] for r in xs if r['sign']=='negative'),
         'zero_correct':sum(r['answer_label']==r['expected'] for r in xs if r['sign']=='zero'),
         'positive_correct':sum(r['answer_label']==r['expected'] for r in xs if r['sign']=='positive'),
         'composed_correct':sum(r['answer_label']==r['expected'] for r in xs if r['family']=='composed')})
    return scores


def run(*,device='cuda',baseline=hybrid.DEFAULT_OUTPUT,backbone=zero.DEFAULT_OUTPUT,
        activation_dir=activation.DEFAULT_RUN,format_dir=format_train.OUTPUT,
        joint_dir=JOINT,output=OUTPUT,cases=6,max_new_tokens=48,depths=(0,1,2,4),
        seed=71,include_joint=True,allow_download=False,loader=None):
    baseline,backbone,activation_dir,format_dir,joint_dir,output=map(Path,
       (baseline,backbone,activation_dir,format_dir,joint_dir,output))
    assert 0 in depths and 4 in depths and all(0<=x<=16 for x in depths),'PAIR_DEPTHS_INVALID'
    assert len(set(depths))==len(depths),'PAIR_DEPTHS_REPEAT'
    for source in (baseline,backbone,activation_dir,format_dir,joint_dir):
        assert output.resolve()!=source.resolve() and source.resolve() not in output.resolve().parents and output.resolve() not in source.resolve().parents,'PAIR_OUTPUT_OVERLAP'
    assert not output.exists() or not any(output.iterdir()),'PAIR_OUTPUT_EXISTS_REFUSED'
    hfmt=format_train.source_hashes(baseline,backbone)
    format_train.assert_provenance(baseline,backbone,hfmt)
    fconfig=json.loads((format_dir/'run_config.json').read_text(encoding='utf8'))
    fresult=json.loads((format_dir/'result.json').read_text(encoding='utf8'))
    assert fconfig['schema']==format_train.SCHEMA and fresult['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED','PAIR_FORMAT_INCOMPLETE'
    assert fconfig['sources']==hfmt,'PAIR_FORMAT_SOURCE_DRIFT'
    fmt_best=torch.load(format_dir/'best_format_lora.pt',map_location='cpu',weights_only=True)
    assert fmt_best['schema']==format_train.SCHEMA and fmt_best['config_hash']==zero.fingerprint(fconfig),'PAIR_FORMAT_CHECKPOINT_DRIFT'
    hc=json.loads((activation_dir/'run_config.json').read_text(encoding='utf8'))
    hr=json.loads((activation_dir/'result.json').read_text(encoding='utf8'))
    assert hc['schema']==activation.SCHEMA and hr['status']=='PASS_MECHANICS_ONLY','PAIR_HEAD_INCOMPLETE'
    assert Path(hc['baseline_dir']).resolve()==baseline.resolve(),'PAIR_HEAD_BASELINE_DRIFT'
    head_best=torch.load(activation_dir/'best_head.pt',map_location='cpu',weights_only=True)
    assert head_best['schema']==activation.SCHEMA and head_best['training_stage']=='activation_only','PAIR_HEAD_SCHEMA_DRIFT'
    lora_c=json.loads((backbone/'run_config.json').read_text(encoding='utf8'))
    lb=torch.load(backbone/'best_lora.pt',map_location='cpu',weights_only=True)
    assert lb['schema']==zero.SCHEMA,'PAIR_LORA_SCHEMA_DRIFT'
    js=None
    if include_joint:
        jc=json.loads((joint_dir/'run_config.json').read_text(encoding='utf8'))
        jr=json.loads((joint_dir/'result.json').read_text(encoding='utf8'))
        assert jc['schema']==joint.SCHEMA and jr['status']=='SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED','PAIR_JOINT_INCOMPLETE'
        assert jc['sources']==joint.source_fingerprints(baseline,activation_dir,backbone),'PAIR_JOINT_SOURCE_DRIFT'
        js=torch.load(joint_dir/'best.pt',map_location='cpu',weights_only=True)
        assert js['schema']==joint.SCHEMA and js['config_sha256']==joint.sha_config(jc),'PAIR_JOINT_CHECKPOINT_DRIFT'
    source_dirs={'base':baseline,'numerical':backbone,'head':activation_dir,'format':format_dir}
    if include_joint:source_dirs['joint']=joint_dir
    fingerprints={str(p):activation.file_hash(p) for d in source_dirs.values() for p in sorted(d.iterdir()) if p.is_file()}
    load=loader or hybrid.load_qwen
    model,tokenizer,details=load(device=device,allow_download=allow_download,
        precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'auto')
    hybrid.load_adapter(baseline,model,details)
    assert details['base_model']==hybrid.MODEL_ID and details['revision']==hybrid.MODEL_REVISION,'PAIR_MODEL_REVISION_DRIFT'
    model.backbone.requires_grad_(False);model.head.requires_grad_(False)
    zero.install_lora(model.backbone,int(lora_c['rank']),float(lora_c['alpha']),
                required_layers=int(model.backbone.config.num_hidden_layers))
    model.head.to(device=device,dtype=torch.float32)
    frozen_hash=zero.frozen_backbone_sha256(model.backbone)
    rows=selected_cases(seed,18,cases)
    configurations=[('numerical_pre_format',lb['lora'],head_best['head']),
                    ('format_activated',fmt_best['lora'],head_best['head'])]
    if include_joint:configurations.append(('previous_joint',js['lora'],js['head']))
    results=[]
    for name,lora_state,head_state in configurations:
        zero.restore_lora(model.backbone,lora_state)
        model.head.load_state_dict(head_state,strict=True)
        for row in rows:
            prompt=f"Question: Compute {row['expression']}.\nReasoning: "
            toks=tokenizer(prompt,add_special_tokens=False)['input_ids']
            assert toks and str(row['answer']) not in prompt.split('Reasoning:')[-1],'PAIR_GOLD_ANSWER_LEAK'
            ids=torch.tensor([toks],dtype=torch.long,device=device)
            for depth in depths:
                controls=['intact','zero_latent'] if depth==4 else ['intact']
                for control in controls:
                    outcome=decode_once(model,tokenizer,ids,depth,max_new_tokens,corrupt=control=='zero_latent')
                    r={'configuration':name,'id':row['id'],'family':row['family'],'sign':row['sign'],
                        'expected':row['answer'],'depth':depth,'control':control,**outcome}
                    results.append(r)
                    print('HYBRID_PAIRED_CASE='+json.dumps(r,sort_keys=True,ensure_ascii=False),flush=True)
        assert zero.frozen_backbone_sha256(model.backbone)==frozen_hash,'PAIR_FROZEN_BACKBONE_MUTATED'
    now={str(p):activation.file_hash(p) for d in source_dirs.values() for p in sorted(d.iterdir()) if p.is_file()}
    assert fingerprints==now,'PAIR_SOURCE_FILE_MUTATION'
    scores=paired_scores(results)
    summary={'schema':SCHEMA,'status':'DIAGNOSTIC_COMPLETE_UNCERTIFIED','configurations':len(configurations),
      'cases_per_configuration':len(rows),'depths':list(depths),'scores':scores,
      'optimizer_steps':0,'oracle_latents_supplied':0,'source_mutations':0,
      'checkpoint_files':{name:str(directory.resolve()) for name,directory in source_dirs.items()},
      'interpretation':'Descriptive paired diagnostic only; no learned mode switching or causal reasoning claim.'}
    output.mkdir(parents=True,exist_ok=True)
    zero.atomic_json(output/'result.json',{'summary':summary,'records':results})
    print('HYBRID_PAIRED_RESULT='+json.dumps(summary,sort_keys=True),flush=True)
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--baseline',type=Path,default=hybrid.DEFAULT_OUTPUT)
    p.add_argument('--backbone',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--activation',type=Path,default=activation.DEFAULT_RUN)
    p.add_argument('--format',type=Path,default=format_train.OUTPUT)
    p.add_argument('--joint',type=Path,default=JOINT)
    p.add_argument('--output',type=Path,default=OUTPUT)
    p.add_argument('--cases',type=int,default=6)
    p.add_argument('--max-new-tokens',type=int,default=48)
    p.add_argument('--skip-joint',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        run(device=args.device,baseline=args.baseline,backbone=args.backbone,
            activation_dir=args.activation,format_dir=args.format,joint_dir=args.joint,
            output=args.output,cases=args.cases,max_new_tokens=args.max_new_tokens,
            include_joint=not args.skip_joint,allow_download=args.allow_model_download)
        return 0
    except (RuntimeError,AssertionError,ValueError,OSError,FileExistsError,KeyError) as exc:
        print('HYBRID_PAIRED_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':sys.exit(main())
