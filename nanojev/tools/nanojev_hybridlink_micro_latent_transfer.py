#!/usr/bin/env python3
"""Train ONLY HybridLink on diverse domain-anonymous ReduceOne transitions.

The Qwen base and successful micro text LoRA are frozen. This is a deliberately
separate, uncertified training lineage: a positive held-out, matched
intact-vs-zero_latent result is required before claiming feedback helps.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random

from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_micro_ood_probe as ood
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA = 'nanojev.hybridlink.anonymous-micro-latent-transfer.v1'
DEPTHS = (2, 4, 6, 10)
SHAPES = ('balanced', 'left', 'right', 'zigzag')
SCALES = (8, 64, 256, 4096, 16384, 1000000)


def _parent_config(path):
    ck = Path(path)
    if not ck.is_file():
        raise ValueError('MICRO_LINK_TEXT_PARENT_MISSING')
    cfg = json.loads((ck.parent / 'run_config.json').read_text(encoding='utf8'))
    if cfg.get('schema') != micro.SCHEMA or cfg.get('stage') != 'train-text':
        raise ValueError('MICRO_LINK_TEXT_PARENT_WRONG_FAMILY')
    return cfg


def _old_deck(cfg):
    params = {k: tuple(cfg[k]) if k in ('band', 'scales') else cfg[k]
              for k in ('band','scales','seed','train_count','dev_count','test_count')}
    deck = micro.deck_for(**params)
    if micro.deck_hash(deck) != cfg['deck_hash']:
        raise RuntimeError('MICRO_LINK_RETENTION_DECK_DRIFT')
    return deck


def _new_cases(count, seed, split):
    if count <= 0: raise ValueError('MICRO_LINK_EMPTY_SPLIT')
    rng = random.Random(seed + {'train': 0, 'dev': 100003}[split])
    cases = []
    seen = set()
    for i in range(count):
        depth = DEPTHS[i % len(DEPTHS)]
        shape = SHAPES[(i//len(DEPTHS)+i)%len(SHAPES)]
        scale = SCALES[(i//4 + i//24)%len(SCALES)]
        delta = (0, 1, -1, 4, -4, 2, -2, 3, -3)[i%9]
        reverse = bool((i//3)%2)
        negative = (i%3 != 0)
        # Use independently generated values and shapes. No OOD fixture index,
        # target, or teacher reduction is ever used as an input to generation.
        for attempt in range(1000):
            anchor = min(3_800_000, max(16, scale) + rng.randint(-3, 31))
            n_a = 1 + rng.randrange(depth-1)
            n_b = depth-n_a
            a = ood._tree(anchor, n_a, rng, shape, negative=negative and n_a>=2)
            b = ood._tree(anchor-delta, n_b, rng, shape,
                          negative=negative and n_b>=2)
            names = micro.REGISTERS[(i+rng.randrange(4))%len(micro.REGISTERS)]
            s = micro.parse(micro.render({'names':names,'a':a,'b':b,'reverse':reverse}))
            markup = micro.render(s)
            if markup not in seen:
                seen.add(markup)
                break
        else: raise RuntimeError('MICRO_LINK_CASE_EXHAUSTED')
        assert micro.steps(s)==depth
        assert micro.result_value(s)==(-delta if reverse else delta)
        cases.append({'id':f'{split}-new-{i:05d}','state':s,
                      'answer':micro.result_value(s), 'depth':depth,
                      'shape':shape,'scale':scale,'negative':negative,'reverse':reverse})
    return cases


def _transition_states(cases):
    return {micro.render(row['state']) for row in micro.training_rows(cases)}


def make_decks(cfg, *, seed=90271, train_count=192, dev_count=48, holdout_guard=True):
    old=_old_deck(cfg)
    train=_new_cases(train_count, seed, 'train')
    dev=_new_cases(dev_count, seed, 'dev')
    train_states=_transition_states(train)
    dev_states=_transition_states(dev)
    if train_states & dev_states:
        raise RuntimeError('MICRO_LINK_NEW_SPLIT_OVERLAP')
    retention_states=_transition_states(old['dev'])
    if train_states & retention_states:
        raise RuntimeError('MICRO_LINK_RETENTION_DEV_OVERLAP')
    if holdout_guard:
        # The existing OOD challenge remains untouched; use its *inputs* only
        # as a one-way overlap guard. Do not select or train on its errors.
        heldout=ood.generate_ood_cases(cfg, seed=20261027)
        heldout_states=set()
        for c in heldout:
            if c['suite']=='control':continue
            state=c['state']
            while micro.steps(state):
                heldout_states.add(micro.render(state))
                state=micro.reduce_one(state)
        if train_states & heldout_states:
            raise RuntimeError('MICRO_LINK_OOD_EVALUATION_LEAK')
    return {'new_train':train,'new_dev':dev,'retention_train':old['train'],
            'retention_dev':old['dev']}


def _fingerprint(decks):
    return base.digest({group:[(c['id'],micro.render(c['state']),c['answer']) for c in rows]
                        for group,rows in decks.items()})


def _sample_dev(rows, size, seed=19):
    # Coverage of signs, operator roles and all positions, not first N rows.
    rng=random.Random(seed)
    buckets=defaultdict(list)
    for row in rows:
        state=row['state']
        nxt=micro.reduce_one(state)
        available=[]
        for source,target in ((state['a'],nxt['a']),(state['b'],nxt['b'])):
            if not micro._same(source,target):available.append(micro._render(source))
        key=(row['position']%4, (row['expected']>0)-(row['expected']<0))
        buckets[key].append(row)
    for val in buckets.values():rng.shuffle(val)
    out=[]
    while len(out)<min(size,len(rows)):
        progressed=False
        for key in sorted(buckets):
            if buckets[key]:out.append(buckets[key].pop());progressed=True
            if len(out)==min(size,len(rows)):break
        if not progressed:break
    return out


def preview(args,cfg):
    decks=make_decks(cfg,seed=args.seed,train_count=args.train_count,
                     dev_count=args.dev_count)
    counts={k:{'cases':len(v),'hops':len(micro.training_rows(v))} for k,v in decks.items()}
    sample=decks['new_train'][1]['state']
    demo=[micro.render(sample)]
    for _ in range(min(3,micro.steps(sample))):
        sample=micro.reduce_one(sample)
        demo.append(micro.render(sample))
    result={'schema':SCHEMA,'stage':'preview','model_loaded':False,
            'text_checkpoint':str(Path(args.text_checkpoint)),
            'deck_sha256':_fingerprint(decks),'counts':counts,
            'sample_transitions':demo,'depths':DEPTHS,'scales':SCALES,
            'heldout_ood_used_for_training':False,'test_evaluated':False}
    print('MICRO_LINK_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)
    return result


def _head_state(model):
    return {k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}


def _load_text_model(args,cfg):
    model,tok,sources,frozen,dirs=micro._load(args,checkpoint=Path(args.text_checkpoint))
    if sources != cfg['sources'] or frozen != cfg['frozen_backbone']:
        raise RuntimeError('MICRO_LINK_TEXT_SOURCE_DRIFT')
    return model,tok,sources,frozen,dirs


def _load_link_into(model,ckpath,cfg,text_sha):
    import torch
    ck=torch.load(ckpath,weights_only=True,map_location='cpu')
    saved=json.loads((Path(ckpath).parent/'run_config.json').read_text(encoding='utf8'))
    if ck.get('schema')!=SCHEMA or ck.get('status')!='LINK_TRAINED':
        raise RuntimeError('MICRO_LINK_CHECKPOINT_SCHEMA')
    if saved.get('schema')!=SCHEMA or base.digest(saved)!=ck.get('config_hash'):
        raise RuntimeError('MICRO_LINK_CONFIG_DRIFT')
    if ck['text_sha256']!=text_sha or saved['text_sha256']!=text_sha:
        raise RuntimeError('MICRO_LINK_TEXT_PARENT_HASH_DRIFT')
    if ck['source_sha256']!=cfg['sources'] or ck['frozen_backbone_sha256']!=cfg['frozen_backbone']:
        raise RuntimeError('MICRO_LINK_SOURCE_DRIFT')
    model.head.load_state_dict(ck['head'],strict=True)
    return ck


def train(args,cfg):
    import torch
    from tools import nanojev_qwen_hybridagent_activation as activation
    from tools import nanojev_qwen_zero_line_backbone as zero
    if not (0 < args.eval_every <= args.steps and args.lr>0 and args.max_seq>=256):
        raise ValueError('MICRO_LINK_BAD_TRAIN_PARAMETERS')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('MICRO_LINK_OUTPUT_NOT_EMPTY')
    decks=make_decks(cfg,seed=args.seed,train_count=args.train_count,
                     dev_count=args.dev_count)
    new_train=micro.training_rows(decks['new_train'])
    old_train=micro.training_rows(decks['retention_train'])
    dev_new=_sample_dev(micro.training_rows(decks['new_dev']),args.dev_samples)
    dev_old=_sample_dev(micro.training_rows(decks['retention_dev']),max(6,args.dev_samples//3))
    model,tok,sources,frozen,dirs=_load_text_model(args,cfg)
    if any(out.resolve()==d.resolve() or out.resolve() in d.resolve().parents or
           d.resolve() in out.resolve().parents for d in (*dirs,Path(args.text_checkpoint).parent)):
        raise RuntimeError('MICRO_LINK_OUTPUT_OVERLAPS_SOURCE')
    model.backbone.requires_grad_(False)
    for _,p in zero.lora_named_parameters(model.backbone):p.requires_grad_(False)
    model.head.requires_grad_(True)
    lora_before=zero.lora_state(model.backbone)
    initial_head=_head_state(model)
    # Fail before creating a run directory if the curriculum will exceed the
    # context budget. Never silently truncate arithmetic or target states.
    for row in (*new_train,*old_train,*dev_new,*dev_old):
        base._encode(tok,row,args.max_seq)
    text_sha=activation.file_hash(Path(args.text_checkpoint))
    config={'schema':SCHEMA,'stage':'train-link','text_checkpoint':str(Path(args.text_checkpoint).resolve()),
            'text_sha256':text_sha,'source_sha256':sources,'frozen_backbone_sha256':frozen,
            'deck_sha256':_fingerprint(decks),'seed':args.seed,'train_count':args.train_count,
            'dev_count':args.dev_count,'dev_eval_new':len(dev_new),'dev_eval_retention':len(dev_old),
            'train_hops_new':len(new_train),'train_hops_retention':len(old_train),
            'max_seq':args.max_seq,'steps':args.steps,'eval_every':args.eval_every,
            'lr':args.lr,'retention_frequency':args.retention_frequency,
            'selection':'0.75 new + 0.25 retention DEV teacher-forced loss (NOT accuracy)',
            'head_only':True,'test_evaluated':False}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'run_config.json',config)
    rng=random.Random(args.seed+133)
    rng.shuffle(new_train);rng.shuffle(old_train)
    optimizer=torch.optim.AdamW(model.head.parameters(),lr=args.lr,weight_decay=0)
    best=float('inf');best_step=None
    torch.manual_seed(args.seed)
    model.backbone.eval();model.head.eval()
    for step in range(1,args.steps+1):
        use_retention=step%args.retention_frequency==0
        pool=old_train if use_retention else new_train
        n=(step//args.retention_frequency if use_retention else step-step//args.retention_frequency)-1
        row=pool[n%len(pool)]
        optimizer.zero_grad(set_to_none=True)
        loss=base.latent_loss(model,tok,row,args.device,args.max_seq)
        if not bool(torch.isfinite(loss).all()):raise RuntimeError('MICRO_LINK_NONFINITE_LOSS')
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.head.parameters(),1.)
        optimizer.step()
        if step%args.eval_every and step!=args.steps:continue
        with torch.no_grad():
            new_loss=sum(float(base.latent_loss(model,tok,r,args.device,args.max_seq)) for r in dev_new)/len(dev_new)
            old_loss=sum(float(base.latent_loss(model,tok,r,args.device,args.max_seq)) for r in dev_old)/len(dev_old)
        selected=.75*new_loss+.25*old_loss
        if selected<best:
            best=selected;best_step=step
            base._atomic_torch(out/'best.pt',{
                'schema':SCHEMA,'status':'LINK_TRAINED','step':step,'dev_selection_loss':selected,
                'dev_new_loss':new_loss,'dev_retention_loss':old_loss,
                'config_hash':base.digest(config),'text_sha256':text_sha,
                'source_sha256':sources,'frozen_backbone_sha256':frozen,
                'head':_head_state(model)})
        print('MICRO_LINK_TRAIN_PROGRESS='+json.dumps({
            'step':step,'loss':round(float(loss.detach()),6),
            'dev_new_loss':round(new_loss,6),'dev_retention_loss':round(old_loss,6),
            'selection_loss':round(selected,6),'best_selection_loss':round(best,6),
            'best_step':best_step,'trained':'hybridlink_head_only'},sort_keys=True),flush=True)
    if zero.frozen_backbone_sha256(model.backbone)!=frozen:
        raise RuntimeError('MICRO_LINK_FROZEN_BACKBONE_MUTATED')
    after=zero.lora_state(model.backbone)
    if any(not torch.equal(v,after[k]) for k,v in lora_before.items()):
        raise RuntimeError('MICRO_LINK_FROZEN_TEXT_ADAPTER_MUTATED')
    if all(torch.equal(v,model.head.state_dict()[k].detach().cpu()) for k,v in initial_head.items()):
        raise RuntimeError('MICRO_LINK_HEAD_NEVER_UPDATED')
    res={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED','best_step':best_step,
         'best_teacher_forced_dev_loss':best,'text_sha256':text_sha,
         'test_evaluated':False,'head_only':True,
         'ood_not_evaluated':True}
    base._atomic_json(out/'result.json',res)
    print('MICRO_LINK_TRAIN_RESULT='+json.dumps(res,sort_keys=True),flush=True)
    return res


def _probe(args,cfg,kind):
    import torch
    from tools import nanojev_qwen_hybridagent_activation as activation
    if kind=='ood':
        # The original OOD deck and its exact scoring remain unchanged.
        cases,_=ood.select_cases(cfg,args.suites,args.limit_per_suite,args.ood_seed)
    else:
        ds=_old_deck(cfg)
        cases=[{'id':f'control-{i:03d}','suite':'control','state':c['state'],
                'operations':micro.steps(c['state']),'answer':c['answer'],'scale':c['scale'],
                'reverse':c['state']['reverse']} for i,c in enumerate(ds['dev'])]
        if args.limit_per_suite:cases=cases[:args.limit_per_suite]
    if not cases:raise ValueError('MICRO_LINK_NO_PROBE_CASES')
    ck=Path(args.link_checkpoint)
    text_sha=activation.file_hash(Path(args.text_checkpoint))
    model,tok,sources,frozen,dirs=_load_text_model(args,cfg)
    _load_link_into(model,ck,cfg,text_sha)
    model.backbone.eval();model.head.eval()
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('MICRO_LINK_PROBE_OUTPUT_NOT_EMPTY')
    if any(out.resolve()==d.resolve() or out.resolve() in d.resolve().parents or
            d.resolve() in out.resolve().parents for d in (*dirs,ck.parent,Path(args.text_checkpoint).parent)):
        raise RuntimeError('MICRO_LINK_PROBE_SOURCE_OVERLAP')
    records=[];chains=[];calls=0
    max_calls=sum(c['operations'] for c in cases)*2*len(args.controls)
    print('MICRO_LINK_PROBE_BEGIN='+json.dumps({'kind':kind,'cases':len(cases),
        'controls':args.controls,'max_calls':max_calls,'text_sha256':text_sha,
        'head_sha256':activation.file_hash(ck),'test_evaluated':False},sort_keys=True),flush=True)
    def evaluate(state,control):
        nonlocal calls
        calls+=1
        if calls==1 or calls%args.progress_every==0:
            print('MICRO_LINK_PROBE_CALL='+json.dumps({'number':calls,
                'control':control,'remaining':micro.steps(state)},sort_keys=True),flush=True)
        generated=base._decode(model,tok,micro.prompt(state),control,args.max_new_tokens,stop_at_end_rung=True)
        return generated,micro.evaluate_transition(state,generated)
    with torch.inference_mode():
        for c in cases:
            for control in args.controls:
                state=c['state']
                for idx in range(c['operations']):
                    generated,score=evaluate(state,control)
                    records.append({'case':c['id'],'suite':c['suite'],'control':control,
                                    'operation_index':idx,'correct':score['valid'],
                                    'reason':score['reason'],'input':micro.render(state),
                                    'generated':generated,'teacher_target':micro.target(state)})
                    state=micro.reduce_one(state)
                state=c['state']; trace=[];ok=True
                while micro.steps(state):
                    generated,score=evaluate(state,control)
                    trace.append({'correct':score['valid'],'reason':score['reason'],'generated':generated})
                    if not score['valid']:
                        ok=False;break
                    state=score['state']
                chains.append({'case':c['id'],'suite':c['suite'],'control':control,
                               'correct':ok and micro.result_value(state)==c['answer'],
                               'steps':len(trace),'trace':trace})
    def metric(rows,chs):
        return {'one_step':{'correct':sum(x['correct'] for x in rows),'total':len(rows),
                            'accuracy':round(sum(x['correct'] for x in rows)/len(rows),6) if rows else None},
                'chains':{'correct':sum(c['correct'] for c in chs),'total':len(chs),
                          'accuracy':round(sum(c['correct'] for c in chs)/len(chs),6) if chs else None}}
    scores={control:{suite:metric([x for x in records if x['control']==control and x['suite']==suite],
                                [x for x in chains if x['control']==control and x['suite']==suite])
                      for suite in sorted(set(c['suite'] for c in cases))}
            for control in args.controls}
    paired={}
    for suite in sorted(set(c['suite'] for c in cases)):
        for control in ('text_only','zero_latent'):
            if not {control,'intact'} <= set(args.controls):continue
            a={(x['case'],x['operation_index']):x['correct'] for x in records if x['suite']==suite and x['control']=='intact'}
            b={(x['case'],x['operation_index']):x['correct'] for x in records if x['suite']==suite and x['control']==control}
            assert a.keys()==b.keys()
            paired[f'{suite}:intact_vs_{control}']={'n':len(a),'intact_only':sum(a[k] and not b[k] for k in a),
                'control_only':sum(b[k] and not a[k] for k in a),'both_correct':sum(a[k] and b[k] for k in a),
                'both_wrong':sum(not a[k] and not b[k] for k in a)}
    result={'schema':SCHEMA,'status':'PROBE_UNCERTIFIED','kind':kind,'cases':len(cases),
            'controls':args.controls,'scores':scores,'paired':paired,
            'generation_calls':calls,'oracle_intermediate_at_inference':False,
            'semantic_domain_words_in_micro_prompts':False,'test_evaluated':False,
            'text_sha256':text_sha,'head_sha256':activation.file_hash(ck),
            'ood_deck_sha256':ood.cases_digest(cases) if kind=='ood' else None,
            'no_training':True}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'result.json',result)
    base._atomic_json(out/'one_steps.json',{'records':records})
    base._atomic_json(out/'free_chains.json',{'records':chains})
    print('MICRO_LINK_PROBE_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','probe-ood','probe-retention'),default='preview')
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--link-checkpoint',help='Required for probe stages; checkpoint from --stage train')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_micro_latent_transfer_v1'))
    p.add_argument('--seed',type=int,default=90271)
    p.add_argument('--train-count',type=int,default=192)
    p.add_argument('--dev-count',type=int,default=48)
    p.add_argument('--steps',type=int,default=1600)
    p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--dev-samples',type=int,default=18)
    p.add_argument('--retention-frequency',type=int,default=4)
    p.add_argument('--lr',type=float,default=0.0001)
    p.add_argument('--max-seq',type=int,default=1024)
    p.add_argument('--max-new-tokens',type=int,default=512)
    p.add_argument('--controls',default='text_only,zero_latent,intact')
    p.add_argument('--suites',default='control,structure,depth,negative,magnitude,direction')
    p.add_argument('--limit-per-suite',type=int)
    p.add_argument('--ood-seed',type=int,default=20261027)
    p.add_argument('--progress-every',type=int,default=10)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.dev_samples<6 or args.retention_frequency<2 or args.progress_every<1:
        p.error('MICRO_LINK_INVALID_TRAIN_OR_PROGRESS_CONFIG')
    if args.train_count<12 or args.dev_count<12 or args.limit_per_suite is not None and args.limit_per_suite<1:
        p.error('MICRO_LINK_BAD_CASE_COUNTS')
    args.controls=[v.strip() for v in args.controls.split(',') if v.strip()]
    args.suites=[v.strip() for v in args.suites.split(',') if v.strip()]
    if len(set(args.controls))!=len(args.controls) or not args.controls or any(c not in ('text_only','zero_latent','intact') for c in args.controls):
        p.error('MICRO_LINK_BAD_CONTROLS')
    if len(set(args.suites))!=len(args.suites) or not args.suites or any(s not in ood.SUITES for s in args.suites):
        p.error('MICRO_LINK_BAD_SUITES')
    cfg=_parent_config(args.text_checkpoint)
    if args.stage=='preview':preview(args,cfg)
    elif args.stage=='train':train(args,cfg)
    else:
        if not args.link_checkpoint:p.error('MICRO_LINK_PROBE_NEEDS_LINK_CHECKPOINT')
        _probe(args,cfg,'ood' if args.stage=='probe-ood' else 'retention')


if __name__=='__main__':main()
