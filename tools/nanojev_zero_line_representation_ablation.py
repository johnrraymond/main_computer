#!/usr/bin/env python3
"""Controlled NanoJev bank feature ablation, no routing or answer oracle.

All three arms use identical signed-sum TRAIN/DEV examples, sampling stream,
optimizer settings, comparable trainable parameter budgets, and independent
checkpoints. Features and digit-token embeddings come from the same immutable
radius-127-certified AdditionNet. The token-only arm still uses its pretrained
input embedding layer; it ablates recurrent backbone features, not pretraining.

The OOD magnitude-fold and adjacent-band stress set are evaluated only AFTER
all arms have selected their checkpoints on DEV. They never train an arm or
select a checkpoint. Labels never enter the inference signature.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from torch import nn

from tools.nanojev_zero_line_absolute_magnitude_banks import (
    AbsoluteBank, atomic_json, atomic_pt, loss_for, score,
)
from tools.nanojev_zero_line_bank_autopilot import GROUPS, sample_band
from tools.nanojev_zero_line_learned_router import cache_with_tokens
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA='nanojev.zero-line-representation-ablation.v1'
ARMS=('backbone','augmented','token_only')
BAND=(192,223)


def parameter_count(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def closest_hidden(input_size, target_parameters, maximum=512):
    """Same two-hidden-layer topology with matched parameter budget."""
    scores=[]
    for hidden in range(8,maximum+1):
        model=nn.Sequential(nn.LayerNorm(input_size),nn.Linear(input_size,hidden),nn.GELU(),
                            nn.Linear(hidden,hidden),nn.GELU(),nn.Linear(hidden,73))
        n=parameter_count(model)
        scores.append((abs(n-target_parameters),hidden,n))
    _,width,count=min(scores)
    if abs(count-target_parameters)/target_parameters>.02:
        raise RuntimeError('ZERO_ABLATION_UNMATCHED_PARAMETER_BUDGET')
    return width,count


class RepresentationBank(nn.Module):
    """Only operand-derived frozen activations may reach the bank."""
    def __init__(self,arm,feature_width,token_width,hidden):
        super().__init__()
        if arm not in ARMS or feature_width<=0 or token_width<=0:raise ValueError('ZERO_ABLATION_BAD_MODEL')
        self.arm=arm
        input_width=feature_width if arm=='backbone' else (token_width if arm=='token_only' else feature_width+token_width)
        self.net=nn.Sequential(nn.LayerNorm(input_width),nn.Linear(input_width,hidden),nn.GELU(),
                               nn.Linear(hidden,hidden),nn.GELU(),nn.Linear(hidden,73))

    def forward(self,features,tokens):
        if self.arm=='backbone':x=features
        elif self.arm=='token_only':x=tokens
        else:x=torch.cat((features,tokens),dim=-1)
        return self.net(x)


def make_models(feature_width,token_width,reference_hidden=256,seed=1):
    target=parameter_count(AbsoluteBank(feature_width,reference_hidden))
    models={}; budgets={}
    for arm in ARMS:
        inp=feature_width if arm=='backbone' else (token_width if arm=='token_only' else feature_width+token_width)
        hidden,count=closest_hidden(inp,target)
        torch.manual_seed(seed+199)
        model=RepresentationBank(arm,feature_width,token_width,hidden)
        assert parameter_count(model)==count
        models[arm]=model
        budgets[arm]={'parameters':count,'hidden':hidden,'target_parameters':target,'relative_parameter_gap':abs(count-target)/target}
    return models,budgets


@torch.inference_mode()
def predict(model,data,batch=2048):
    model.eval()
    return torch.cat([model(data['features'][i:i+batch],data['tokens'][i:i+batch])
                      for i in range(0,len(data['target']),batch)],0)


def make_groups(train):
    pools={g:[] for g in GROUPS}
    for i,y in enumerate(train['target'].tolist()):
        a=abs(y)
        group='zero' if a==0 else 'near' if a<=2 else 'inner' if a<=15 else 'frontier' if a<=32 else 'mid' if a<=64 else 'far'
        pools[group].append(i)
    if any(not x for x in pools.values()):raise RuntimeError('ZERO_ABLATION_MISSING_TRAIN_GROUP')
    return pools


def meta_for(args,arm,source,budgets):
    return {'schema':SCHEMA,'arm':arm,'source_sha256':source['checkpoint_sha256'],
            'band':list(BAND),'seed':args.seed,'per_cell':args.sample_per_cell,
            'steps':args.steps,'probe_every':args.probe_every,'batch_size':args.batch_size,
            'lr':args.lr,'feature_width':args.feature_width,
            'token_width':args.token_width,'budget':budgets[arm]}


def train_arm(model,train,dev,output,meta,*,steps,probe_every,batch_size,lr,seed):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    state_path=output/'training.pt';best_path=output/'best.pt'
    opt=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=0.)
    rng=random.Random(seed)
    best=-1.;best_step=0;start=0;history=[]
    if state_path.exists():
        state=torch.load(state_path,map_location='cpu',weights_only=True)
        if state['meta']!=meta:raise RuntimeError('ZERO_ABLATION_RESUME_CONFIG_MISMATCH')
        model.load_state_dict(state['model'],strict=True)
        opt.load_state_dict(state['optimizer'])
        rng.setstate(state['rng'])
        best=state['best'];best_step=state['best_step'];start=state['step'];history=state['history']
        if start>steps:raise RuntimeError('ZERO_ABLATION_STEP_ROLLBACK')
    pools=make_groups(train)
    weights=('zero','near','near','inner','frontier','frontier','mid','far','far')
    for step in range(start+1,steps+1):
        ids=torch.tensor([rng.choice(pools[rng.choice(weights)]) for _ in range(batch_size)],device=train['target'].device)
        model.train()
        logits=model(train['features'][ids],train['tokens'][ids])
        loss=loss_for(logits,train['target'][ids])
        opt.zero_grad(set_to_none=True);loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.);opt.step()
        if step%probe_every and step!=steps:continue
        dev_score=score(predict(model,dev),dev)
        tr_score=score(predict(model,train),train)
        row={'step':step,'loss':float(loss.detach()),'train':tr_score['accuracy'],'dev':dev_score['accuracy']}
        history.append(row)
        if dev_score['accuracy']>best:
            best=dev_score['accuracy'];best_step=step
            atomic_pt(best_path,{'meta':meta,'step':step,'model':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()}})
        atomic_pt(state_path,{'meta':meta,'step':step,'model':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()},
                              'optimizer':opt.state_dict(),'rng':rng.getstate(),'best':best,
                              'best_step':best_step,'history':history})
        print(f'ZERO_ABLATION arm={model.arm} step={step}/{steps} loss={row["loss"]:.5f} '
              f'TRAIN={row["train"]:.5f} DEV={row["dev"]:.5f} best={best:.5f}',flush=True)
    if not best_path.exists():raise RuntimeError('ZERO_ABLATION_NO_BEST_CHECKPOINT')
    best_payload=torch.load(best_path,map_location='cpu',weights_only=True)
    if best_payload['meta']!=meta:raise RuntimeError('ZERO_ABLATION_BEST_META_MISMATCH')
    model.load_state_dict(best_payload['model'],strict=True)
    model.eval().requires_grad_(False)
    return {'selected_step':best_payload['step'],'best_dev_accuracy':best,'history':history}


def safe_output(output,input_roots):
    out=Path(output).expanduser().resolve()
    for path in input_roots:
        p=Path(path).expanduser().resolve()
        if out==p or out in p.parents or p in out.parents:
            raise RuntimeError('ZERO_ABLATION_OUTPUT_INPUT_OVERLAP')
    return out


def validate_split(samples,seed):
    sets={s:{(a,b) for a,b,*_ in rows} for s,rows in samples.items()}
    if any(not x for x in sets.values()):raise RuntimeError('ZERO_ABLATION_EMPTY_SPLIT')
    for name in ('train','dev','ood'):
        for a,b,y,*_ in samples[name]:
            if y!=a+b:raise RuntimeError('ZERO_ABLATION_BAD_TEACHER')
            m=max(abs(a),abs(b))
            if not BAND[0]<=m<=BAND[1]:raise RuntimeError('ZERO_ABLATION_WRONG_MAGNITUDE')
    if any(sets[x]&sets[y] for x,y in (('train','dev'),('train','ood'),('dev','ood'))):
        raise RuntimeError('ZERO_ABLATION_LEAKED_PAIR')
    # The sampler hashes unordered absolute operand pairs so sign/swap mirrors
    # must never leak across TRAIN and DEV, even if raw ordered pairs differ.
    def families(split):
        return {tuple(sorted((abs(a),abs(b)))) for a,b,*_ in samples[split]}
    if families('train') & families('dev'):
        raise RuntimeError('ZERO_ABLATION_LEAKED_MIRRORED_FAMILY')
    train_m={max(abs(a),abs(b)) for a,b,*_ in samples['train']+samples['dev']}
    ood_m={max(abs(a),abs(b)) for a,b,*_ in samples['ood']}
    if train_m&ood_m:raise RuntimeError('ZERO_ABLATION_LEAKED_MAGNITUDE')


def baseline_score(stage,source,dev,ood,frontier,device,feature_width):
    stage=Path(stage).expanduser().resolve()
    config=json.loads((stage/'config.json').read_text(encoding='utf-8'))
    if config.get('band')!=list(BAND) or config.get('source_sha256')!=source['checkpoint_sha256']:
        raise RuntimeError('ZERO_ABLATION_BASELINE_PROVENANCE_MISMATCH')
    hidden=config['training_meta']['bank_hidden']
    bank=AbsoluteBank(feature_width,int(hidden)).to(device)
    state=torch.load(stage/'bank_best.pt',map_location='cpu',weights_only=True)
    bank.load_state_dict(state['bank'],strict=True)
    bank.eval().requires_grad_(False)
    with torch.inference_mode():
        return {'selected_step':int(state['step']),
                'dev':score(bank(dev['features']),dev),
                'ood':score(bank(ood['features']),ood),
                'adjacent_stress':score(bank(frontier['features']),frontier),
                'checkpoint_sha256':sha256(stage/'bank_best.pt')}


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('ZERO_ABLATION_CUDA_UNAVAILABLE')
    if any(n<=0 for n in (args.steps,args.probe_every,args.batch_size,args.eval_batch_size,
                         args.sample_per_cell,args.cpu_threads,args.reference_hidden)) or not 0<args.lr<1:
        raise ValueError('ZERO_ABLATION_BAD_OPTIONS')
    roots=[args.ladder_output]+([args.baseline_stage] if args.baseline_stage else [])
    output=safe_output(args.output,roots)
    if not args.ladder_output:raise ValueError('ZERO_ABLATION_SOURCE_REQUIRED')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    base,source=load_certified_model(args.ladder_output,args.device)
    base.eval().requires_grad_(False)
    samples=sample_band(*BAND,args.seed,args.sample_per_cell)
    validate_split(samples,args.seed)
    # This is a previously unseen *adjacent magnitude* stress test, distinct
    # from the held-out maximum-magnitude fold within the assigned 192:223 band.
    neighbor=sample_band(224,255,args.seed+7,args.sample_per_cell, splits=('ood',))['ood']
    if not neighbor or any(BAND[0]<=max(abs(a),abs(b))<=BAND[1] for a,b,*_ in neighbor):
        raise RuntimeError('ZERO_ABLATION_INVALID_ADJACENT_HOLDOUT')
    cache={k:cache_with_tokens(base,v,args.device,args.eval_batch_size,True) for k,v in samples.items()}
    adjacent=cache_with_tokens(base,neighbor,args.device,args.eval_batch_size,True)
    fw=int(cache['train']['features'].shape[-1]);tw=int(cache['train']['tokens'].shape[-1]);
    args.feature_width=fw;args.token_width=tw
    models,budgets=make_models(fw,tw,args.reference_hidden,args.seed)
    setup={'schema':SCHEMA,'source':source,'band':list(BAND),'seed':args.seed,'sample_per_cell':args.sample_per_cell,
           'steps':args.steps,'probe_every':args.probe_every,'batch_size':args.batch_size,'lr':args.lr,
           'reference_hidden':args.reference_hidden,'budgets':budgets,
           'split_counts':{s:len(rows) for s,rows in samples.items()},'adjacent_cards':len(neighbor),
           'baseline_stage':str(Path(args.baseline_stage).expanduser().resolve()) if args.baseline_stage else None}
    output.mkdir(parents=True,exist_ok=True)
    cfg=output/'config.json'
    if cfg.exists() and json.loads(cfg.read_text(encoding='utf-8'))!=setup:
        raise RuntimeError('ZERO_ABLATION_CONFIGURATION_CHANGED')
    if not cfg.exists():atomic_json(cfg,setup)
    results={}
    for arm in ARMS:
        model=models[arm].to(args.device)
        meta=meta_for(args,arm,source,budgets)
        progress=train_arm(model,cache['train'],cache['dev'],output/arm,meta,
                           steps=args.steps,probe_every=args.probe_every,batch_size=args.batch_size,
                           lr=args.lr,seed=args.seed+173)
        results[arm]={'parameters':budgets[arm], **progress}
    # OOD is not evaluated until every arm has finished selection on DEV.
    for arm in ARMS:
        model=models[arm]
        results[arm]['train']=score(predict(model,cache['train']),cache['train'])
        results[arm]['dev']=score(predict(model,cache['dev']),cache['dev'])
        results[arm]['ood']=score(predict(model,cache['ood']),cache['ood'])
        results[arm]['adjacent_stress']=score(predict(model,adjacent),adjacent)
        print('ZERO_ABLATION_RESULT='+json.dumps({'arm':arm,'step':results[arm]['selected_step'],
              'dev':results[arm]['dev']['accuracy'],'ood':results[arm]['ood']['accuracy'],
              'adjacent':results[arm]['adjacent_stress']['accuracy'],
              'params':results[arm]['parameters']['parameters']},sort_keys=True),flush=True)
    reference=baseline_score(args.baseline_stage,source,cache['dev'],cache['ood'],adjacent,args.device,fw) if args.baseline_stage else None
    if sha256(source['checkpoint'])!=source['checkpoint_sha256']:
        raise RuntimeError('ZERO_ABLATION_SOURCE_MUTATED')
    report={'schema':SCHEMA,'config':setup,'arms':results,'existing_bank_baseline':reference,
            'source_unchanged':True,'no_router':True,'no_answer_features':True,
            'ood_not_used_for_training_or_selection':True,
            'interpretation':'token_only uses pretrained input embeddings; tests recurrent-state contribution, not random-init control'}
    atomic_json(output/'result.json',report)
    print('ZERO_ABLATION_SUMMARY='+json.dumps({'band':list(BAND),
          'arms':{k:{'dev':v['dev']['accuracy'],'ood':v['ood']['accuracy'],
                     'adjacent':v['adjacent_stress']['accuracy']} for k,v in results.items()},
          'report':str(output/'result.json')},sort_keys=True),flush=True)
    print('ZERO_ABLATION_BACKBONE_FROZEN=TRUE TEST_UNTOUCHED=TRUE NO_ROUTING=TRUE',flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--baseline-stage',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps',type=int,default=6000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--sample-per-cell',type=int,default=96)
    p.add_argument('--reference-hidden',type=int,default=256)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):return run(parser().parse_args(argv))

if __name__=='__main__':main()
