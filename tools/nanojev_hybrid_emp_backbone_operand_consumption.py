#!/usr/bin/env python3
"""Certify Qwen transformer consumption of frozen Head 2 operand edits.

The only trainable weights are Qwen attention LoRA. The native Qwen language
head must decode each operand into a single-token value code, separately for
operand A and operand B, without receiving raw numerical-token embeddings.
No arithmetic target is ever used in training or inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import torch
from torch.nn import functional as F

from tools import nanojev_hybrid_emp_backbone_zero_line as prior
from tools import nanojev_hybrid_emp_join as join
from tools import nanojev_hybrid_emp_operand_stage as operand

SCHEMA = 'nanojev.hybrid-emp-qwen-operand-consumption.v1'
ROOT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_hybrid_emp_qwen_operand_consumption_v1'
VALUE_RANGE = range(-16, 17)
SUFFIXES = ('\nFirst operand code: ', '\nSecond operand code: ')


def _single_logits(backbone, vectors, class_ids):
    return prior.select_logits(backbone, vectors, class_ids)


def paired_logits(backbone, tokenizer, embedding, head1, sender, card, style, device,
                  class_ids, *, mode='intact'):
    with torch.no_grad():
        ex = join.extract(card,style,tokenizer,head1,embedding,device,7)
        if ex['values'] != [card.a,card.b]:
            raise RuntimeError('CONSUME_OPERAND_PROVENANCE_DRIFT')
        ex['bits'] = operand.bits(ex['values'],device)
    outputs=[]
    for suffix in SUFFIXES:
        vectors=prior.sanitize_numerical_embeddings(ex,sender,tokenizer,embedding,
                                                     mode=mode,suffix_text=suffix)
        outputs.append(_single_logits(backbone,vectors,class_ids))
    return torch.stack(outputs)


def gradient_step(backbone,optimizer,tokenizer,embedding,head1,sender,card,style,device,
                  class_ids):
    backbone.train()
    optimizer.zero_grad(set_to_none=True)
    with torch.autocast(device_type='cuda' if device=='cuda' else 'cpu',
                        enabled=device=='cuda',dtype=torch.bfloat16 if device=='cuda' else torch.float32):
        outputs=paired_logits(backbone,tokenizer,embedding,head1,sender,card,style,device,class_ids)
        targets=torch.tensor([card.a+16,card.b+16],device=device,dtype=torch.long)
        loss=F.cross_entropy(outputs,targets)
    if not bool(torch.isfinite(loss)):
        raise RuntimeError('CONSUME_NONFINITE_LOSS')
    loss.backward()
    report=prior.lora_tools.gradient_report(backbone)
    if report['missing'] or report['nonfinite'] or not report['nonzero']:
        raise RuntimeError('CONSUME_BAD_LORA_GRADIENT')
    if any(param.grad is not None for name,param in backbone.named_parameters()
           if not name.endswith(('.lora_a','.lora_b'))):
        raise RuntimeError('CONSUME_FROZEN_PARAMETER_GRADIENT')
    torch.nn.utils.clip_grad_norm_([p for _,p in prior.lora_tools.lora_named_parameters(backbone)],
                                   1.,error_if_nonfinite=True)
    optimizer.step()
    return float(loss.detach()),float((outputs.argmax(dim=1)==targets).float().mean().detach())


def probe_cards(cards, per_value=2):
    """Deterministically sample representative DEV pairs; TEST never sampled."""
    needs={n:per_value for n in VALUE_RANGE}
    selected=[]
    for card in sorted(cards,key=lambda c:hashlib.sha256(
            f'probe-consumption:{c.a}:{c.b}'.encode()).digest()):
        if needs[card.a] or needs[card.b]:
            selected.append(card)
            needs[card.a]=max(0,needs[card.a]-1)
            needs[card.b]=max(0,needs[card.b]-1)
    if any(needs.values()):
        raise RuntimeError('CONSUME_DEV_VALUE_COVERAGE_MISSING')
    return selected


def evaluate(backbone,tokenizer,embedding,head1,sender,cards,device,class_ids,*,
             styles=(0,), controls=True):
    if not cards:raise RuntimeError('CONSUME_EMPTY_EVALUATION')
    previous=backbone.training
    backbone.eval()
    correct={'intact':0,'zero':0,'wrong':0}
    pairs={'intact':0,'zero':0,'wrong':0}
    by_value={str(n):{'correct':0,'total':0} for n in VALUE_RANGE}
    by_role={str(i):{'correct':0,'total':0} for i in range(2)}
    n=0
    try:
        with torch.inference_mode():
            for card in cards:
                for style in styles:
                    truth=torch.tensor([card.a+16,card.b+16],device=device)
                    modes=('intact','zero','wrong') if controls else ('intact',)
                    for mode in modes:
                        logits=paired_logits(backbone,tokenizer,embedding,head1,sender,
                                             card,style,device,class_ids,mode=mode)
                        hits=logits.argmax(dim=1)==truth
                        correct[mode]+=int(hits.sum())
                        pairs[mode]+=int(bool(hits.all()))
                        if mode=='intact':
                            for role,value in enumerate((card.a,card.b)):
                                cell=by_value[str(value)]
                                cell['correct']+=int(hits[role]);cell['total']+=1
                                by_role[str(role)]['correct']+=int(hits[role]);by_role[str(role)]['total']+=1
                    n+=1
    finally:
        backbone.train(previous)
    for v in by_value.values():
        v['accuracy']=v['correct']/v['total'] if v['total'] else 0.
    for v in by_role.values():v['accuracy']=v['correct']/v['total'] if v['total'] else 0.
    return {'pairs':n,'operands':n*2,'pair_exact':pairs['intact']/n,
            'operand_exact':correct['intact']/(n*2),'zero_pair_exact':pairs['zero']/n,
            'wrong_pair_exact':pairs['wrong']/n,
            'by_value':by_value,'by_role':by_role,
            'minimum_value_accuracy':min(v['accuracy'] for v in by_value.values()),
            'controls_evaluated':controls}


def passes(m,*,expected_values=VALUE_RANGE):
    if not m.get('controls_evaluated') or m.get('pairs',0)<1:
        return False
    cells=m.get('by_value',{})
    if set(cells)!={str(v) for v in expected_values}:
        return False
    if any(x.get('total',0)<2 or x.get('accuracy',0)<.98 for x in cells.values()):
        return False
    return (m.get('pair_exact',0)>=.98 and m.get('operand_exact',0)>=.98 and
            m['pair_exact']-m.get('zero_pair_exact',1)>=.10 and
            m['pair_exact']-m.get('wrong_pair_exact',1)>=.10)


def _metadata(device,identity,hashes,seed,rank,alpha,lr,eval_every,steps):
    return {'schema':SCHEMA,'device':device,'identity':identity,
            'operand_hashes':hashes,'seed':seed,'rank':rank,'alpha':alpha,
            'lr':lr,'eval_every':eval_every,'steps':steps,
            'source_sha256':prior.digest(__file__),
            'backbone_source_sha256':prior.digest(prior.__file__)}


def run(*,device='cuda',output=ROOT,head1_dir=join.HEAD1,operand_dir=operand.ROOT,
        model_loader=None,head1_override=None,allow_download=False,seed=20261026,
        rank=8,alpha=16.,lr=.0002,steps=1800,eval_every=100,stop_after=None,resume=False):
    if device not in ('cpu','cuda') or lr<=0 or rank<1 or steps<1 or eval_every<1:
        raise ValueError('CONSUME_BAD_CONFIG')
    pop=prior.stratified_provider(seed)
    dev_probe=probe_cards(pop.decks['dev'])
    backbone,tokenizer,embedding,head1,sender,identity,hashes=prior.load_certified_stack(
        device=device,head1_dir=head1_dir,operand_dir=operand_dir,
        allow_download=allow_download,model_loader=model_loader,head1_override=head1_override)
    prior.lora_tools.install_lora(backbone,rank,alpha,
        required_layers=getattr(backbone.config,'num_hidden_layers',None))
    opt=torch.optim.AdamW([p for _,p in prior.lora_tools.lora_named_parameters(backbone)],
                          lr=lr,weight_decay=0.)
    codes=prior.class_codes(tokenizer,16)
    class_ids=prior.code_indices(codes,16,device)
    config=_metadata(device,identity,hashes,seed,rank,alpha,lr,eval_every,steps)
    root=Path(output)
    if resume:
        if (root/'gate_result.json').exists():raise RuntimeError('CONSUME_ALREADY_GATED')
        if json.loads((root/'config.json').read_text('utf8')) != config:
            raise RuntimeError('CONSUME_RESUME_CONFIG_DRIFT')
        state=torch.load(root/'last.pt',map_location='cpu',weights_only=True)
        if state['schema']!=SCHEMA:raise RuntimeError('CONSUME_RESUME_SCHEMA')
        prior.lora_tools.restore_lora(backbone,state['lora'])
        opt.load_state_dict(state['optimizer'])
        torch.set_rng_state(state['torch_rng'])
        if device=='cuda':torch.cuda.set_rng_state_all(state['cuda_rng'])
        start=state['step'];streak=state['streak'];best=state['best']
    else:
        if root.exists() and any(root.iterdir()):raise RuntimeError('CONSUME_OUTPUT_EXISTS')
        torch.manual_seed(seed)
        if device=='cuda':torch.cuda.manual_seed_all(seed)
        root.mkdir(parents=True,exist_ok=True)
        prior.atomic_json(root/'config.json',config)
        start=0;streak=0;best=-1.
    if device=='cuda':torch.cuda.reset_peak_memory_stats()
    status='CONSUMPTION_DEV_FAIL'
    for step in range(start+1,steps+1):
        # Value sampling is independent of prompt rendering; cover both roles.
        card=pop.rng.choice(pop.decks['train'])
        style=(step//2)%5
        loss,accuracy=gradient_step(backbone,opt,tokenizer,embedding,head1,sender,card,
                                    style,device,class_ids)
        if step==1 or step%eval_every==0 or step==steps:
            dev=evaluate(backbone,tokenizer,embedding,head1,sender,dev_probe,device,
                         class_ids,styles=(0,),controls=True)
            passed=passes(dev)
            streak=streak+1 if passed else 0
            objective=dev['minimum_value_accuracy']+dev['pair_exact']+.001*max(
                0.,dev['pair_exact']-max(dev['zero_pair_exact'],dev['wrong_pair_exact']))
            if objective>best:
                best=objective
                prior.atomic_torch(root/'best.pt',{'schema':SCHEMA,'step':step,
                    'lora':prior._lora_snapshot(backbone),'dev_probe':dev})
            event={'step':step,'total':steps,'loss':round(loss,6),
                   'train_operand_accuracy':accuracy,'dev_pair_exact':dev['pair_exact'],
                   'dev_minimum_value_accuracy':dev['minimum_value_accuracy'],
                   'dev_zero_pair_exact':dev['zero_pair_exact'],
                   'dev_wrong_pair_exact':dev['wrong_pair_exact'],
                   'consecutive_passes':streak,'dev_pass':passed,'best_objective':best}
            if device=='cuda':event['cuda_peak_mib']=round(torch.cuda.max_memory_allocated()/1048576,1)
            print('HYBRID_CONSUME_STEP='+json.dumps(event,sort_keys=True),flush=True)
        state={'schema':SCHEMA,'step':step,'streak':streak,'best':best,
               'optimizer':opt.state_dict(),'lora':prior._lora_snapshot(backbone),
               'torch_rng':torch.get_rng_state(),
               'cuda_rng':torch.cuda.get_rng_state_all() if device=='cuda' else None}
        prior.atomic_torch(root/'last.pt',state)
        if stop_after is not None and step-start>=stop_after:
            status='PAUSED_RESUMABLE';break
        if streak>=2:
            status='TRAINED_UNCERTIFIED';break
    result={'schema':SCHEMA,'status':status,'step':step,
            'consecutive_passes':streak,'arithmetic_trained':False,'test_read':False,
            'trainable_scope':'Qwen attention LoRA only'}
    prior.atomic_json(root/'training_result.json',result)
    print('HYBRID_CONSUME_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def gate(*,device='cuda',output=ROOT,head1_dir=join.HEAD1,operand_dir=operand.ROOT,
         model_loader=None,head1_override=None,allow_download=False):
    root=Path(output)
    if (root/'gate_result.json').exists():raise RuntimeError('CONSUME_TEST_ALREADY_READ')
    cfg=json.loads((root/'config.json').read_text('utf8'))
    result=json.loads((root/'training_result.json').read_text('utf8'))
    if cfg['schema']!=SCHEMA or result['status']!='TRAINED_UNCERTIFIED' or cfg['device']!=device:
        raise RuntimeError('CONSUME_DEV_CERTIFICATION_REQUIRED')
    backbone,tokenizer,embedding,head1,sender,identity,hashes=prior.load_certified_stack(
        device=device,head1_dir=head1_dir,operand_dir=operand_dir,
        allow_download=allow_download,model_loader=model_loader,head1_override=head1_override)
    if cfg != _metadata(device,identity,hashes,cfg['seed'],cfg['rank'],cfg['alpha'],
                        cfg['lr'],cfg['eval_every'],cfg['steps']):
        raise RuntimeError('CONSUME_GATE_PROVENANCE_DRIFT')
    prior.lora_tools.install_lora(backbone,cfg['rank'],cfg['alpha'],
        required_layers=getattr(backbone.config,'num_hidden_layers',None))
    saved=torch.load(root/'best.pt',map_location='cpu',weights_only=True)
    if saved['schema']!=SCHEMA:raise RuntimeError('CONSUME_CHECKPOINT_SCHEMA')
    prior.lora_tools.restore_lora(backbone,saved['lora'])
    pop=prior.stratified_provider(cfg['seed'])
    ids=prior.code_indices(prior.class_codes(tokenizer,16),16,device)
    dev=evaluate(backbone,tokenizer,embedding,head1,sender,pop.decks['dev'],device,
                 ids,styles=(0,1),controls=True)
    if passes(dev):
        test=evaluate(backbone,tokenizer,embedding,head1,sender,pop.decks['test'],device,
                      ids,styles=(0,1,2),controls=True)
        success=passes(test)
        report={'schema':SCHEMA,'status':'BACKBONE_OPERAND_CONSUMPTION_PASS' if success
                else 'BACKBONE_OPERAND_CONSUMPTION_FAIL','gate_pass':success,
                'dev':dev,'test':test,'test_read':True,'arithmetic_trained':False,
                'checkpoint_step':saved['step']}
    else:
        report={'schema':SCHEMA,'status':'CONSUMPTION_FULL_DEV_FAIL',
                'gate_pass':False,'dev':dev,'test_read':False,'arithmetic_trained':False}
    prior.atomic_json(root/'gate_result.json',report)
    print('HYBRID_CONSUME_GATE='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('all','preview','train','gate'),default='all')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--output',type=Path,default=ROOT)
    p.add_argument('--operand-output',type=Path,default=operand.ROOT)
    p.add_argument('--head1-output',type=Path,default=join.HEAD1)
    p.add_argument('--steps',type=int,default=1800)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--lr',type=float,default=.0002)
    p.add_argument('--rank',type=int,default=8)
    p.add_argument('--alpha',type=float,default=16.)
    p.add_argument('--seed',type=int,default=20261026)
    p.add_argument('--stop-after',type=int)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.stage=='preview':
        pop=prior.stratified_provider(args.seed)
        selected=probe_cards(pop.decks['dev'])
        print('HYBRID_CONSUME_PREVIEW='+json.dumps({'schema':SCHEMA,
            'splits':{k:len(v) for k,v in pop.decks.items()},
            'probe_pairs':len(selected),'class_count':33,
            'arithmetic_targets_used':False,'requires_head2_gate':True,
            'original_number_embeddings_masked':True},sort_keys=True))
        return 0
    kw=dict(device=args.device,output=args.output,operand_dir=args.operand_output,
            head1_dir=args.head1_output,allow_download=args.allow_model_download)
    if args.stage in ('all','train'):
        report=run(**kw,seed=args.seed,rank=args.rank,alpha=args.alpha,lr=args.lr,
                   steps=args.steps,eval_every=args.eval_every,stop_after=args.stop_after,
                   resume=args.resume)
        if report['status']!='TRAINED_UNCERTIFIED':return 0
    if args.stage in ('all','gate'):gate(**kw)
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,ValueError,OSError,AssertionError,FileNotFoundError) as exc:
        print('HYBRID_CONSUME_REFUSED='+str(exc),file=sys.stderr)
        sys.exit(2)
