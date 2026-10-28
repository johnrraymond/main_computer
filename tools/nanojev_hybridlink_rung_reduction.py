#!/usr/bin/env python3
"""Experimental learned HybridLink rung reduction: T08 -> B0b -> B0a -> T0 -> answer.

This is a separate research lineage, NOT the certified QW5 Stage 2. No source
model or certified numerical checkpoint is modified. Teacher arithmetic is used
only to construct supervised targets and held-out evaluation; never injected at
inference, and a failed generated hop never receives an oracle replacement.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import re

SCHEMA = 'nanojev.hybridlink.rung-reduction.v1'
CHAIN = ('T08', 'B0b', 'B0a', 'T0', 'ANSWER')
DEFAULT_OUTPUT = Path.home() / 'NanoJev' / 'runs' / 'hybridlink_rung_reduction_v1'
TERMINATOR = 'END_RUNG'


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def scalelist(value):
    scales = tuple(int(x.strip()) for x in value.split(',') if x.strip())
    if not scales or len(scales) != len(set(scales)) or min(scales) < 2 or max(scales) > 4_000_000:
        raise ValueError('RUNG_INVALID_SCALES')
    return scales


def canonical_chain(a: int, delta: int, variant: int = 0, reverse: bool = False):
    """One answer-preserving chain, not a numerical-radius reduction.

    Payoffs satisfy (HH-DH)=1 and (HD-DD)=-1. Direction changes which
    subtraction is requested, with identical operands in the reverse pair.
    """
    b = a - delta
    if not 0 < a <= 4_000_000 or not 0 < b <= 4_000_000:
        raise ValueError('RUNG_INPUT_OUT_OF_BOUNDS')
    hh, dh, hd, dd = 2 + variant, 1 + variant, 1 + variant, 2 + variant
    x = a * hh + b * hd
    y = a * dh + b * dd
    expected = y - x if reverse else x - y
    assert x-y == delta and expected == (-delta if reverse else delta)
    sign = 'Dove-minus-Hawk' if reverse else 'Hawk-minus-Dove'
    original = (f'In Hawk-Dove, an opponent chooses Hawk {a} times and Dove {b} times. '
                f'Choosing Hawk pays {hh} versus Hawk and {hd} versus Dove; '
                f'choosing Dove pays {dh} versus Hawk and {dd} versus Dove. '
                f'What is the exact signed {sign} advantage?')
    b0b = (f'Compute Hawk total = ({a}*{hh}) + ({b}*{hd}) and '
           f'Dove total = ({a}*{dh}) + ({b}*{dd}). '
           f'What is the signed {sign} difference?')
    b0a = f'Hawk total is {x}; Dove total is {y}. What is the signed {sign} difference?'
    first, second = (y,x) if reverse else (x,y)
    t0 = f'Compute {first} + (-{second}). Return its exact signed integer value.'
    texts = (original, b0b, b0a, t0)
    return {'texts': dict(zip(CHAIN[:-1], texts)), 'answer': expected,
            'direction':sign, 'a':a, 'b':b, 'x':x, 'y':y, 'delta':delta,
            'reverse':reverse, 'variant':variant}


def hop_prompt(rung: str, question: str):
    if rung not in CHAIN[:-1] or not question.strip():
        raise ValueError('RUNG_BAD_PROMPT')
    instruction = ('Return the final signed integer.' if rung == 'T0' else
                   'Rewrite as the next simpler question. Preserve the signed direction; do not answer yet.')
    return (f'Task: Reduce exactly one rung. {instruction}\n'
            f'Current rung: {rung}\nQuestion: {question}\nNext: ')


def hop_target(chain, rung):
    if rung not in CHAIN[:-1]:
        raise ValueError('RUNG_BAD_TARGET')
    next_rung = CHAIN[CHAIN.index(rung)+1]
    content = (str(chain['answer']) if next_rung == 'ANSWER' else chain['texts'][next_rung])
    return f'[{next_rung}] {content}\n{TERMINATOR}\n'


def case_key(chain):
    return (chain['a'],chain['b'],chain['variant'],chain['reverse'])


def generate_deck(*, band=(0,4), scales=(8,16,32,64), seed=71,
                  train_count=36, dev_count=12, test_count=12):
    """Scenario-disjoint splits. Each scenario yields four supervised hops.

    Held-out scenarios have different operand/payoff/direction combinations,
    not merely a different surface prompt for an identical equation. Delta
    signs are represented in each split. Repeated residual values across splits
    are unavoidable with a bounded 0-4 band and are reported explicitly.
    """
    lo,hi=band
    if not (0 <= lo <= hi <= 4_000_000):
        raise ValueError('RUNG_BAD_BAND')
    if min(scales) < 2*max(1,hi) or max(scales)>4_000_000:
        raise ValueError('RUNG_SCALE_BELOW_TWICE_DELTA_OR_ABOVE_4M')
    if any(n < 6 or n%6 for n in (train_count,dev_count,test_count)):
        raise ValueError('RUNG_SPLIT_COUNTS_MULTIPLE_OF_SIX')
    rng=random.Random(seed)
    magnitudes=tuple(dict.fromkeys((*range(max(1,lo),min(hi,15)+1),hi))) if hi else ()
    magnitudes=tuple(k for k in magnitudes if k>0)
    seen=set()
    deck={}
    for split,count in (('train',train_count),('dev',dev_count),('test',test_count)):
        rows=[]
        for i in range(count):
            delta = (0 if i%3==0 or not magnitudes else
                     (1 if i%3==1 else -1)*magnitudes[(i//3 + {'train':0,'dev':3,'test':6}[split])%len(magnitudes)])
            reverse = bool((i//3)%2)
            scale = scales[(i//6 + {'train':0,'dev':1,'test':2}[split])%len(scales)]
            found=None
            for _ in range(1000):
                # Different anchors and payoff variants allow held-out cases
                # even at the minimum scale (8) and narrow residual bands.
                anchor=rng.randint(2*max(1,hi),max(2*max(1,hi),scale))
                variant=rng.randrange(6)
                if anchor-delta>4_000_000:continue
                ch=canonical_chain(anchor,delta,variant,reverse)
                if case_key(ch) not in seen:
                    found=ch;break
            if found is None:raise ValueError('RUNG_DECK_EXHAUSTED_INCREASE_SCALES_OR_VARIANTS')
            seen.add(case_key(found))
            found['case_id']=f'{split}-{i:05d}'
            rows.append(found)
        deck[split]=rows
    assert not (set(map(case_key,deck['train'])) & set(map(case_key,deck['dev'])))
    assert not (set(map(case_key,deck['train'])) & set(map(case_key,deck['test'])))
    assert not (set(map(case_key,deck['dev'])) & set(map(case_key,deck['test'])))
    return deck


def flatten_hops(cases):
    rows=[]
    for c in cases:
        for rung in CHAIN[:-1]:
            rows.append({'case_id':c['case_id'], 'rung':rung,
                         'prompt':hop_prompt(rung,c['texts'][rung]),
                         'target':hop_target(c,rung),'answer':c['answer'],
                         'sign':'zero' if c['answer']==0 else 'positive' if c['answer']>0 else 'negative'})
    return rows


_REDUCTION = re.compile(r'^\[([A-Za-z0-9]+)\] ([^\n]+)\nEND_RUNG\s*', re.S)


def parse_hop(text: str, from_rung: str):
    """Extract a single complete hop. Reject junk before first target.

    We do not take a later correct candidate after a malformed first hop.
    A generated continuation beyond END_RUNG is logged as a format violation.
    """
    match=_REDUCTION.match(text.lstrip())
    wanted=CHAIN[CHAIN.index(from_rung)+1]
    if not match:
        return {'valid':False,'reason':'missing_complete_hop','next_rung':None,'question':None}
    rung, content=match.group(1),match.group(2)
    if rung!=wanted:
        return {'valid':False,'reason':'wrong_rung','next_rung':rung,'question':content}
    if rung=='ANSWER':
        if not re.fullmatch(r'[+-]?\d+',content):
            return {'valid':False,'reason':'non_integer_answer','next_rung':rung,'question':content}
        content=str(int(content))
    return {'valid':True,'reason':None,'next_rung':rung,'question':content,
            'extra_after_hop':bool(text.lstrip()[match.end():].strip())}


def score_hop(text,case,rung):
    parsed=parse_hop(text,rung)
    expected=hop_target(case,rung)
    if not parsed['valid']:
        return {'correct':False,'reason':parsed['reason'],**parsed}
    label=hop_target(case,rung).split('\n',1)[0]
    actual=f'[{parsed["next_rung"]}] {parsed["question"]}'
    return {'correct':actual==label,'reason':None if actual==label else 'content_mismatch',**parsed}


def _source_setup(device,baseline,backbone,activation_dir,format_dir,allow_download):
    from tools import nanojev_hybridlink_prompt_learning_probe as probe
    return probe._source_setup(device,baseline,backbone,activation_dir,format_dir,
                               Path('__unused_joint__'),False,allow_download)


def _paths(args):
    from tools import nanojev_qwen_hybridagent as hybrid
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    from tools import nanojev_qwen_hybridagent_format_activation as fmt
    return tuple(Path(p) for p in (args.baseline or hybrid.DEFAULT_OUTPUT,
           args.backbone or zero.DEFAULT_OUTPUT,
           args.activation or activation.DEFAULT_RUN,
           args.format or fmt.OUTPUT))


def _load(args, *, trained=None):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    baseline, backbone, activation_dir, format_dir=_paths(args)
    model,tok,configs,dirs,hashes,frozen=_source_setup(args.device,baseline,backbone,
               activation_dir,format_dir,args.allow_model_download)
    label,lora,head=next((c for c in configs if c[0]=='format_activated'))
    zero.restore_lora(model.backbone,lora)
    model.head.load_state_dict(head,strict=True)
    if trained is not None:
        ck=torch.load(trained,map_location='cpu',weights_only=True)
        if ck.get('schema')!=SCHEMA or ck.get('status') not in ('TEXT_TRAINED','LINK_TRAINED'):
            raise RuntimeError('RUNG_CHECKPOINT_SCHEMA_OR_STATUS')
        if ck['source_sha256']!=hashes:
            raise RuntimeError('RUNG_SOURCE_HASH_DRIFT')
        zero.restore_lora(model.backbone,ck['lora'])
        model.head.load_state_dict(ck['head'],strict=True)
    return model,tok,hashes,frozen,dirs


def _encode(tok,row,max_seq):
    prompt=tok(row['prompt'],add_special_tokens=False)['input_ids']
    target=tok(row['target'],add_special_tokens=False)['input_ids']
    if not prompt or not target or len(prompt)+len(target)>max_seq:
        raise ValueError('RUNG_TOKEN_LIMIT_EXCEEDED_ADJUST_MAX_SEQ')
    return prompt,target


def text_loss(model,tok,row,device,max_seq):
    import torch
    from torch.nn import functional as F
    p,t=_encode(tok,row,max_seq)
    ids=torch.tensor([p+t[:-1]],device=device,dtype=torch.long)
    decoder=model.backbone.get_decoder()
    embed=model.backbone.get_input_embeddings()
    output=model.backbone.get_output_embeddings()
    states=decoder(inputs_embeds=embed(ids),attention_mask=torch.ones_like(ids),
                   return_dict=True,use_cache=False).last_hidden_state
    logits=output(states[0,len(p)-1:len(p)-1+len(t)].to(dtype=output.weight.dtype)).float()
    truth=torch.tensor(t,device=device,dtype=torch.long)
    return F.cross_entropy(logits,truth)


def latent_loss(model,tok,row,device,max_seq):
    """One learned latent step supervised to emit NEXT PROMPT, not answer.

    Qwen/LoRA are frozen. The head receives the Qwen state after <latent>.
    The entire teacher target is scored after </latent>. No gold hidden state
    or oracle answer is ever passed into the link.
    """
    import torch
    from torch.nn import functional as F
    p,t=_encode(tok,row,max_seq)
    embed=model.backbone.get_input_embeddings()
    decoder=model.backbone.get_decoder()
    output=model.backbone.get_output_embeddings()
    prefix=torch.tensor([p+[model.start_id]],device=device,dtype=torch.long)
    with torch.no_grad():
        pre=decoder(inputs_embeds=embed(prefix),attention_mask=torch.ones_like(prefix),
                    return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
    # Train only HybridLink, through a real next-token loss after one latent.
    next_embedding=model.head(pre.to(dtype=next(model.head.parameters()).dtype)).to(embed.weight.dtype)
    suffix=torch.tensor([[model.end_id]+t[:-1]],device=device,dtype=torch.long)
    seq=torch.cat((embed(prefix).detach(), next_embedding[:,None,:],embed(suffix).detach()),dim=1)
    states=decoder(inputs_embeds=seq,return_dict=True,use_cache=False).last_hidden_state
    first=len(p)+2  # </latent> predicts first target token; latent position predicts </latent>
    logits=output(states[0,first:first+len(t)].to(dtype=output.weight.dtype)).float()
    truth=torch.tensor(t,device=device,dtype=torch.long)
    return F.cross_entropy(logits,truth)


def _atomic_json(path,payload):
    from tools import nanojev_qwen_zero_line_backbone as zero
    zero.atomic_json(path,payload)


def _atomic_torch(path,payload):
    from tools import nanojev_qwen_zero_line_backbone as zero
    zero.atomic_torch(path,payload)


def train(args, stage):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    if stage not in ('train-text','train-link'):raise ValueError('RUNG_INVALID_TRAIN_STAGE')
    if args.steps<1 or args.eval_every<1 or args.eval_every>args.steps or args.lr<=0:
        raise ValueError('RUNG_INVALID_TRAIN_PARAMETERS')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('RUNG_OUTPUT_NOT_EMPTY')
    deck=generate_deck(band=tuple(args.band),scales=args.scales,seed=args.seed,
              train_count=args.train_count,dev_count=args.dev_count,test_count=args.test_count)
    rows=flatten_hops(deck['train']); dev=flatten_hops(deck['dev'])
    parent=Path(args.text_checkpoint) if stage=='train-link' else None
    if parent is not None:
        if not parent.is_file():raise RuntimeError('RUNG_TEXT_CHECKPOINT_MISSING')
        parent_cfg=json.loads((parent.parent/'run_config.json').read_text(encoding='utf8'))
        if parent_cfg.get('schema')!=SCHEMA or parent_cfg.get('stage')!='train-text' or (
                parent_cfg.get('deck_hash')!=digest(deck) or
                parent_cfg.get('band')!=list(args.band) or
                parent_cfg.get('scales')!=list(args.scales)):
            raise RuntimeError('RUNG_TEXT_PARENT_DATASET_DRIFT')
    model,tok,hashes,frozen,dirs=_load(args,trained=parent)
    for d in dirs:
        if out.resolve()==d.resolve() or out.resolve() in d.resolve().parents or d.resolve() in out.resolve().parents:
            raise RuntimeError('RUNG_OUTPUT_OVERLAPS_FROZEN_SOURCE')
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(stage=='train-link')
    if stage=='train-text':
        params=[p for _,p in zero.lora_named_parameters(model.backbone)]
        for p in params:p.requires_grad_(True)
        model.head.requires_grad_(False)
        objective=text_loss
    else:
        for _,p in zero.lora_named_parameters(model.backbone):p.requires_grad_(False)
        params=list(model.head.parameters())
        objective=latent_loss
    # Witness unaffected parameters to prevent a diagnostic experiment from
    # silently mutating its counterpart or the frozen source backbone.
    head_before={k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()} if stage=='train-text' else None
    lora_before=zero.lora_state(model.backbone) if stage=='train-link' else None
    rng=random.Random(args.seed+931)
    rng.shuffle(rows)
    opt=torch.optim.AdamW(params,lr=args.lr,weight_decay=0.0)
    out.mkdir(parents=True,exist_ok=False)
    config={'schema':SCHEMA,'stage':stage,'sources':hashes,'frozen_backbone':frozen,
            'band':list(args.band),'scales':list(args.scales),'seed':args.seed,
            'train_count':args.train_count,'dev_count':args.dev_count,'test_count':args.test_count,
            'deck_hash':digest(deck),'steps':args.steps,'eval_every':args.eval_every,
            'lr':args.lr,'max_seq':args.max_seq,'parent_text_sha256':
              activation.file_hash(parent) if parent else None,
            'train_hops':len(rows),'dev_hops':len(dev),'heldout_test_hops':len(flatten_hops(deck['test']))}
    _atomic_json(out/'run_config.json',config)
    best=float('inf');winner=None
    torch.manual_seed(args.seed)
    for step in range(1,args.steps+1):
        row=rows[(step-1)%len(rows)]
        model.backbone.eval();model.head.eval()  # deterministic; enables gradients
        opt.zero_grad(set_to_none=True)
        loss=objective(model,tok,row,args.device,args.max_seq)
        if not bool(torch.isfinite(loss).all()):raise RuntimeError('RUNG_NONFINITE_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params,1.0)
        opt.step()
        if step%args.eval_every!=0 and step!=args.steps:continue
        with torch.no_grad():
            scores=[float(objective(model,tok,r,args.device,args.max_seq)) for r in dev[:min(12,len(dev))]]
        dev_loss=sum(scores)/len(scores)
        if dev_loss<best:
            best=dev_loss
            winner={'schema':SCHEMA,'status':'TEXT_TRAINED' if stage=='train-text' else 'LINK_TRAINED',
                    'source_sha256':hashes,'frozen_backbone_sha256':frozen,
                    'config_hash':digest(config),'step':step,'dev_loss':dev_loss,
                    'lora':zero.lora_state(model.backbone),
                    'head':{k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}}
            _atomic_torch(out/'best.pt',winner)
        print('RUNG_TRAIN_PROBE='+json.dumps({'stage':stage,'step':step,'train_loss':round(float(loss),5),
              'dev_teacher_forced_loss':round(dev_loss,5),'best_dev_loss':round(best,5),
              'trained':'lora' if stage=='train-text' else 'hybridlink_head'},sort_keys=True),flush=True)
    if zero.frozen_backbone_sha256(model.backbone)!=frozen:
        raise RuntimeError('RUNG_FROZEN_BACKBONE_MUTATED')
    if head_before is not None and any(not torch.equal(model.head.state_dict()[k].detach().cpu(),v)
                                       for k,v in head_before.items()):
        raise RuntimeError('RUNG_TEXT_STAGE_MODIFIED_HYBRIDLINK')
    if lora_before is not None and any(not torch.equal(dict(zero.lora_named_parameters(model.backbone))[k].detach().cpu(),v)
                                       for k,v in lora_before.items()):
        raise RuntimeError('RUNG_LINK_STAGE_MODIFIED_LORA')
    _atomic_json(out/'result.json',{'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED','stage':stage,
       'best_step':winner['step'],'best_dev_teacher_forced_loss':best,
       'next':'run --stage probe --split dev (and held-out test once only after decisions)'})
    print('RUNG_TRAIN_RESULT='+json.dumps({'output':str(out),'stage':stage,'status':'TRAINED_UNCERTIFIED',
        'best_step':winner['step'],'test_evaluated':False},sort_keys=True),flush=True)


def _rung_finished(text):
    """Only the full END_RUNG line is a safe optional decoding stop."""
    return bool(re.search(r'(?m)^END_RUNG(?:\r?\n|$)', text))


def _decode(model,tok,prompt,control,max_new_tokens, *, stop_at_end_rung=False):
    import torch
    from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
    ids=tok(prompt,add_special_tokens=False)['input_ids']
    t=torch.tensor([ids],dtype=torch.long,device=next(model.parameters()).device)
    if control not in ('text_only','zero_latent','intact'):
        raise ValueError('RUNG_BAD_CONTROL')
    depth=0 if control=='text_only' else 1
    if not stop_at_end_rung:
        return paired.decode_once(model,tok,t,depth,max_new_tokens,
                                  corrupt=control=='zero_latent')['completion']
    # Probe-only fast decoding; identical greedy tokens/latent protocol until
    # the COMPLETE END_RUNG line, then stop instead of generating more tasks.
    # Training, teacher-forced loss, checkpoints and baseline decode are unchanged.
    original_head=model.head
    if control=='zero_latent':model.head=paired.ZeroLatent(original_head)
    try:
        with torch.inference_mode():
            if depth:
                _,_,cache,mask,_=model.rollout(t,depth,return_trace=True)
                marker=torch.tensor([[model.end_id]],dtype=torch.long,device=t.device)
                mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
                _,logits,cache=model._step(input_ids=marker,cache=cache,mask=mask)
            else:
                _,logits,cache,mask,_=model.rollout(t,0)
            generated=[]
            for _ in range(max_new_tokens):
                token=logits.argmax(dim=-1,keepdim=True)
                token_id=int(token.item())
                if tok.eos_token_id is not None and token_id==tok.eos_token_id:
                    break
                generated.append(token_id)
                if _rung_finished(tok.decode(generated,skip_special_tokens=True)):
                    break
                mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
                _,logits,cache=model._step(input_ids=token,cache=cache,mask=mask)
            return tok.decode(generated,skip_special_tokens=True)
    finally:
        model.head=original_head


def probe(args):
    import torch
    from tools import nanojev_qwen_hybridagent_activation as activation
    ckpath=Path(args.checkpoint)
    if not ckpath.is_file():raise RuntimeError('RUNG_PROBE_NEEDS_CHECKPOINT')
    # Exact deck generator configuration is in the parent run, not supplied anew.
    saved=json.loads((ckpath.parent/'run_config.json').read_text(encoding='utf8'))
    if saved['schema']!=SCHEMA:raise RuntimeError('RUNG_CONFIG_SCHEMA')
    deck=generate_deck(band=tuple(saved['band']),scales=tuple(saved['scales']),
        seed=saved['seed'],train_count=saved['train_count'],dev_count=saved['dev_count'],
        test_count=saved['test_count'])
    if digest(deck)!=saved['deck_hash']:raise RuntimeError('RUNG_DATASET_DRIFT')
    out=Path(args.output)
    if out.resolve()==ckpath.resolve() or out.resolve()==ckpath.parent.resolve() or out.resolve() in ckpath.parent.resolve().parents or ckpath.parent.resolve() in out.resolve().parents:
        raise RuntimeError('RUNG_PROBE_OUTPUT_OVERLAPS_TRAIN_RUN')
    if out.exists() and any(out.iterdir()):raise RuntimeError('RUNG_PROBE_OUTPUT_NOT_EMPTY')
    if args.split=='test' and (ckpath.parent/'test_consumed.json').exists():
        raise RuntimeError('RUNG_TEST_ALREADY_CONSUMED')
    model,tok,hashes,frozen,dirs=_load(args,trained=ckpath)
    if hashes!=saved['sources'] or frozen!=saved['frozen_backbone']:
        raise RuntimeError('RUNG_PROBE_SOURCE_DRIFT')
    model.backbone.eval();model.head.eval()
    all_cases=deck[args.split]
    if args.case_limit is not None:
        if args.case_limit<1 or args.case_limit>len(all_cases):
            raise RuntimeError('RUNG_PROBE_CASE_LIMIT_OUT_OF_RANGE')
        if args.split=='test':
            raise RuntimeError('RUNG_TEST_REQUIRES_COMPLETE_SPLIT')
    cases=all_cases[:args.case_limit] if args.case_limit is not None else all_cases
    completed_generations=0
    max_generations=len(cases)*len(args.controls)*2*(len(CHAIN)-1)
    print('RUNG_PROBE_BEGIN='+json.dumps({'split':args.split,'cases_selected':len(cases),
          'cases_available':len(all_cases),'controls':list(args.controls),
          'max_generation_calls':max_generations,'max_new_tokens':args.max_new_tokens,
          'stop_at_end_rung':args.stop_at_end_rung,'subset':len(cases)!=len(all_cases)},sort_keys=True),flush=True)
    def generate(prompt,control,case_id,rung,kind):
        nonlocal completed_generations
        if args.progress_every==1:
            print('RUNG_PROBE_CALL='+json.dumps({'case_id':case_id,'control':control,
                  'rung':rung,'kind':kind,'call_number':completed_generations+1},sort_keys=True),flush=True)
        completion=_decode(model,tok,prompt,control,args.max_new_tokens,
                           stop_at_end_rung=args.stop_at_end_rung)
        completed_generations+=1
        if completed_generations%args.progress_every==0:
            print('RUNG_PROBE_PROGRESS='+json.dumps({'completed_calls':completed_generations,
                  'max_calls':max_generations,'case_id':case_id,'control':control,
                  'rung':rung,'kind':kind},sort_keys=True),flush=True)
        return completion
    records=[];chain_records=[]
    one_hop_records=[]
    with torch.inference_mode():
        for case in cases:
            for control in args.controls:
                # Teacher-input ONE-HOP diagnostic at every rung. These are
                # independent tests, never the free-running chain's inputs.
                for rung in CHAIN[:-1]:
                    prompt=hop_prompt(rung,case['texts'][rung])
                    completion=generate(prompt,control,case['case_id'],rung,'one_hop')
                    parsed=score_hop(completion,case,rung)
                    one_hop_records.append({'case_id':case['case_id'],'rung':rung,
                       'control':control,'correct':parsed['correct'],'reason':parsed['reason'],
                       'expected_target':hop_target(case,rung),'completion':completion,
                       'reverse':case['reverse'],'direction':case['direction'],
                       'extra_after_hop':parsed.get('extra_after_hop',False)})
                # Separate FREE-CHAIN diagnostic: starting only from T08.
                # Failed reductions are never replaced with the teacher target.
                current=case['texts']['T08'];success=True;visited=[]
                for rung in CHAIN[:-1]:
                    prompt=hop_prompt(rung,current)
                    completion=generate(prompt,control,case['case_id'],rung,'free_chain')
                    result=score_hop(completion,case,rung)
                    rec={'case_id':case['case_id'],'rung':rung,'control':control,'split':args.split,
                         'expected_next':CHAIN[CHAIN.index(rung)+1],
                         'correct':result['correct'],'reason':result['reason'],
                         'completion':completion,'expected_target':hop_target(case,rung),
                         'extra_after_hop':result.get('extra_after_hop',False)}
                    records.append(rec);visited.append(rung)
                    if not result['correct']:
                        success=False;break
                    if rung!='T0':current=result['question']
                chain_records.append({'case_id':case['case_id'],'control':control,
                    'passed':success,'reached':visited,'expected_answer':case['answer'],
                    'direction':case['direction']})
    out.mkdir(parents=True,exist_ok=False)
    groups=defaultdict(list)
    for r in chain_records:groups[r['control']].append(r)
    totals={c:{'cases':len(rows),'full_chain_correct':sum(x['passed'] for x in rows),
               'full_chain_accuracy':round(sum(x['passed'] for x in rows)/len(rows),5)} for c,rows in groups.items()}
    one_hop={(control,rung):{
                'n':sum(x['control']==control and x['rung']==rung for x in one_hop_records),
                'correct':sum(x['control']==control and x['rung']==rung and x['correct'] for x in one_hop_records),
                'extra_output':sum(x['control']==control and x['rung']==rung and x['extra_after_hop']
                                   for x in one_hop_records)}
           for control in args.controls for rung in CHAIN[:-1]}
    first={c:one_hop[(c,'T08')] for c in args.controls}
    one_hop_by_rung={ctrl:{r:one_hop[(ctrl,r)] for r in CHAIN[:-1]} for ctrl in args.controls}
    result={'schema':SCHEMA,'status':'PROBE_UNCERTIFIED','split':args.split,
            'checkpoint_sha256':activation.file_hash(ckpath),'sources':hashes,
            'chain':list(CHAIN),'one_hop_T08':first,'one_hop_by_rung':one_hop_by_rung,'full_chain':totals,
            'selected_cases':len(cases),'available_cases':len(all_cases),
            'subset_probe':len(cases)!=len(all_cases),'generation_calls':completed_generations,
            'stop_at_end_rung':args.stop_at_end_rung,
            'oracle_intermediate_at_inference':False,'test_evaluated':args.split=='test'}
    _atomic_json(out/'result.json',result)
    _atomic_json(out/'records.json',{'teacher_input_one_hops':one_hop_records,
                                    'free_chain_hops':records,'chains':chain_records})
    if args.split=='test':
        _atomic_json(ckpath.parent/'test_consumed.json',{'schema':SCHEMA,'probe_output':str(out.resolve()),
                                                        'checkpoint_sha256':result['checkpoint_sha256']})
    print('RUNG_PROBE_RESULT='+json.dumps(result,sort_keys=True),flush=True)


def preview(args):
    deck=generate_deck(band=tuple(args.band),scales=args.scales,seed=args.seed,
             train_count=args.train_count,dev_count=args.dev_count,test_count=args.test_count)
    sample=deck['train'][0]
    result={'schema':SCHEMA,'stage':'preview','model_loaded':False,'band':list(args.band),
            'scales':list(args.scales),'chain':list(CHAIN),'scenario_counts':
            {s:len(rows) for s,rows in deck.items()},'teacher_hop_counts':
            {s:len(flatten_hops(rows)) for s,rows in deck.items()},
            'first_example':[{'rung':r,'prompt':hop_prompt(r,sample['texts'][r]),
                              'target':hop_target(sample,r)} for r in CHAIN[:-1]],
            'split_overlap':False,'answer_in_initial_prompt':False}
    print('RUNG_REDUCTION_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage',choices=('preview','train-text','train-link','probe'),default='preview')
    ap.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    ap.add_argument('--band',default='0-4',help='nonnegative residual magnitudes, e.g. 0-4')
    ap.add_argument('--scales',default='8,16,32,64,128,256',help='comma-separated operand scales, <=4M')
    ap.add_argument('--seed',type=int,default=71)
    ap.add_argument('--train-count',type=int,default=36)
    ap.add_argument('--dev-count',type=int,default=12)
    ap.add_argument('--test-count',type=int,default=12)
    ap.add_argument('--steps',type=int,default=200)
    ap.add_argument('--eval-every',type=int,default=25)
    ap.add_argument('--lr',type=float,default=5e-6)
    ap.add_argument('--max-seq',type=int,default=384)
    ap.add_argument('--max-new-tokens',type=int,default=128)
    ap.add_argument('--baseline')
    ap.add_argument('--backbone')
    ap.add_argument('--activation')
    ap.add_argument('--format')
    ap.add_argument('--allow-model-download',action='store_true')
    ap.add_argument('--text-checkpoint',help='Required for train-link: best.pt from train-text')
    ap.add_argument('--checkpoint',help='Required for probe: best.pt from text or link training')
    ap.add_argument('--split',choices=('dev','test'),default='dev')
    ap.add_argument('--controls',default='text_only,zero_latent,intact')
    ap.add_argument('--case-limit',type=int,help='DEV only: evaluate first N cases as a smoke probe')
    ap.add_argument('--progress-every',type=int,default=1,help='Report every N generation calls (default 1)')
    ap.add_argument('--stop-at-end-rung',action='store_true',help='Stop generation after the first complete END_RUNG line')
    ap.add_argument('--output',default=str(DEFAULT_OUTPUT))
    args=ap.parse_args(argv)
    a,b=args.band.split('-',1)
    args.band=(int(a),int(b))
    args.scales=scalelist(args.scales)
    args.controls=tuple(s.strip() for s in args.controls.split(',') if s.strip())
    if not args.controls or any(c not in ('text_only','zero_latent','intact') for c in args.controls):
        ap.error('Unsupported --controls')
    if args.progress_every<1:
        ap.error('--progress-every must be >= 1')
    if args.stage!='probe' and (args.case_limit is not None or args.stop_at_end_rung):
        ap.error('--case-limit and --stop-at-end-rung require --stage probe')
    if args.stage=='preview':preview(args)
    elif args.stage=='probe':probe(args)
    else:
        if args.stage=='train-link' and not args.text_checkpoint:ap.error('--text-checkpoint required for train-link')
        train(args,args.stage)


if __name__=='__main__':main()
