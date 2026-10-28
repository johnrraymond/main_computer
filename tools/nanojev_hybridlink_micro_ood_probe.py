#!/usr/bin/env python3
"""Read-only OOD stress test of an existing anonymous micro-reduction checkpoint.

This is NOT training, not an arithmetic executor for inference, and not QW5
certification.  The parser/validator computes answers offline for scoring only.
All model-facing prompts remain domain-free, exactly as in the trained probe.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import json
from pathlib import Path
import random

from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA='nanojev.hybridlink.anonymous-micro-ood.v1'
SUITES=('control','structure','depth','negative','magnitude','direction')
CAP=4_000_000


def _bin(lhs, op, rhs):
    return ast.BinOp(left=lhs,op={'add':ast.Add,'sub':ast.Sub,'mul':ast.Mult}[op](),right=rhs)


def _literal(x):
    return ast.Constant(int(x))


def _shape_split(n,shape,depth):
    if shape=='left':return n-1,0
    if shape=='right':return 0,n-1
    if shape=='zigzag':return (n-1,0) if depth%2 else (0,n-1)
    return (n-1)//2, n-1-(n-1)//2


def _tree(value,ops,rng,shape,cap=CAP,negative=False,depth=0):
    """Construct exactly ops nodes evaluating to value, with bounded literals.

    The oracle uses this only to synthesize held-out problem *inputs*.  The
    next-state model receives no evaluated intermediate values.
    """
    assert isinstance(value,int) and 0<=ops<=40 and abs(value)<=cap
    if ops==0:return _literal(value)
    if negative and ops>=2 and value>=0 and value<cap-32:
        # First reachable operation computes a strictly NEGATIVE intermediate.
        k=7+rng.randrange(19)
        left=_bin(_literal(k), 'sub', _literal(2*k))
        right=_tree(value+k,ops-2,rng,shape,cap,False,depth+1)
        return _bin(left,'add',right)
    left_ops,right_ops=_shape_split(ops,shape,depth)
    options=['add','sub','mul']
    rng.shuffle(options)
    for op in options:
        wiggle=1+rng.randrange(min(2048,max(2,abs(value)//4+1)))
        if op=='mul':
            divisors=[x for x in (2,3,4,5) if value%x==0]
            if not divisors:continue
            divisor=rng.choice(divisors)
            lv,rv=value//divisor,divisor
        elif op=='add':
            if value>=0:
                lv,rv=value-wiggle,wiggle
            else:
                lv,rv=value+wiggle,-wiggle
        else: # subtraction
            if value>=0:
                lv,rv=value-wiggle,-wiggle
            else:
                lv,rv=value+wiggle,wiggle
        if abs(lv)>cap or abs(rv)>cap:continue
        return _bin(_tree(lv,left_ops,rng,shape,cap,False,depth+1),op,
                    _tree(rv,right_ops,rng,shape,cap,False,depth+1))
    raise AssertionError('OOD_GENERATOR_CANNOT_SPLIT')


def _iter_nodes(n):
    yield n
    if isinstance(n,ast.BinOp):
        yield from _iter_nodes(n.left)
        yield from _iter_nodes(n.right)
    elif isinstance(n,ast.UnaryOp):
        yield from _iter_nodes(n.operand)


def _negative_intermediate(s):
    return any(isinstance(node,ast.BinOp) and micro._value(node)<0
          for field in ('a','b') for node in _iter_nodes(s[field]))


def _max_literal(s):
    return max(abs(n.value) for field in ('a','b') for n in _iter_nodes(s[field])
        if isinstance(n,ast.Constant) and type(n.value) is int)


def _signature(s):
    def signature(node):
        if isinstance(node,ast.BinOp):
            return (type(node.op).__name__,signature(node.left),signature(node.right))
        return 'L'
    return (signature(s['a']),signature(s['b']))


def _case(suite,index,*,operations,anchor,delta,reverse,shape,seed=20261027,
          negative=False,force_max_literal=False,names=None):
    if operations<2 or operations>40:raise ValueError('OOD_INVALID_DEPTH')
    if abs(delta)>4:raise ValueError('OOD_DELTA_OUT_OF_BAND')
    rng=random.Random(seed+101*index+100003*SUITES.index(suite))
    reg=names or micro.REGISTERS[index%len(micro.REGISTERS)]
    # Preserve a real 4-million *input literal* when testing the outer bound.
    n_a=0 if force_max_literal else operations//2
    n_b=operations-n_a
    a_target=anchor
    b_target=anchor-delta
    a=_literal(a_target) if n_a==0 else _tree(a_target,n_a,rng,shape,negative=negative)
    b=_tree(b_target,n_b,rng,shape,negative=negative and n_a==0)
    state={'names':tuple(reg),'a':a,'b':b,'reverse':bool(reverse)}
    # Render/parse *before* any model call, exercising the exact public grammar.
    state=micro.parse(micro.render(state))
    answer=(-delta if reverse else delta)
    assert micro.steps(state)==operations,(suite,operations,micro.steps(state))
    assert micro.result_value(state)==answer
    assert _max_literal(state)<=CAP
    if force_max_literal:assert _max_literal(state)==CAP
    if negative and operations>=4 and anchor>=0 and not force_max_literal:
        assert _negative_intermediate(state)
    return {'id':f'{suite}-{index:03d}','suite':suite,'state':state,
       'operations':operations,'scale':anchor,'delta':delta,'reverse':reverse,
       'answer':answer,'shape':shape,'max_literal':_max_literal(state),
       'negative_intermediate':_negative_intermediate(state),
       'signature':_signature(state)}


def generate_ood_cases(cfg,seed=20261027,include_controls=True):
    cases=[]
    if include_controls:
        options=dict(band=tuple(cfg['band']),scales=tuple(cfg['scales']),seed=cfg['seed'],
            train_count=cfg['train_count'],dev_count=cfg['dev_count'],test_count=cfg['test_count'])
        deck=micro.deck_for(**options)
        if micro.deck_hash(deck)!=cfg['deck_hash']:
            raise RuntimeError('MICRO_OOD_CONTROL_DECK_DRIFT')
        for i,original in enumerate(deck['dev']):
            s=original['state']
            cases.append({'id':f'control-{i:03d}','suite':'control','state':s,
                'operations':micro.steps(s),'scale':original['scale'],
                'delta':original['delta'],'reverse':s['reverse'],
                'answer':micro.result_value(s),'shape':'original_dev',
                'max_literal':_max_literal(s),'negative_intermediate':_negative_intermediate(s),
                'signature':_signature(s)})
    # Structural variation and arbitrary depth: these are NOT the two-sums-of-
    # two-products pattern used for training.
    for i,n in enumerate((2,6,10,20,2,6,10,20)):
        cases.append(_case('structure',i,operations=n,anchor=179+i*31,
                 delta=(0,1,-1,4,-4,2,-2,3)[i],reverse=bool(i%2),
                 shape=('balanced','right','left','zigzag')[i%4],seed=seed))
    for i,n in enumerate((2,2,6,6,10,10,20,20)):
        cases.append(_case('depth',i,operations=n,anchor=4096+i*37,
             delta=(0,1,-1,4,-4,2,-2,3)[i],reverse=i%2==0,
             shape=('left','balanced','right','zigzag')[i%4],seed=seed))
    for i,n in enumerate((6,10,20,6,10,20)):
        cases.append(_case('negative',i,operations=n,anchor=513+i*59,
             delta=(0,1,-1,4,-4,2)[i],reverse=bool(i%2),
             shape=('balanced','right','zigzag')[i%3],seed=seed,negative=True))
    for i,anchor in enumerate((8,256,4096,65536,1000000,CAP)):
        cases.append(_case('magnitude',i,operations=(2,6,10,20,6,10)[i],
           anchor=anchor,delta=(0,1,-1,4,-4,2)[i],reverse=bool(i%2),
           shape=('left','balanced','right','zigzag')[i%4],seed=seed,
           force_max_literal=anchor==CAP))
    for i,delta in enumerate((4,4,-4,-4,0,0)):
        # Matched forward/reverse pair: same numbers and tree, different ASK.
        pair=i//2
        case=_case('direction',pair,operations=(6,10,20)[pair],
           anchor=1673+pair*401,delta=delta,reverse=False,
           shape=('balanced','right','zigzag')[pair],seed=seed)
        if i%2:
            case={**case,'state':{**case['state'],'reverse':True},'reverse':True,
                  'answer':-delta}
        case['id']=f'direction-{i:03d}'
        cases.append(case)
    # Distinct initial expression states across all new OOD cases (except
    # explicitly paired reverse-direction controls).  Distinct from TRAIN.
    train_cfg=dict(band=tuple(cfg['band']),scales=tuple(cfg['scales']),seed=cfg['seed'],
       train_count=cfg['train_count'],dev_count=cfg['dev_count'],test_count=cfg['test_count'])
    train=micro.deck_for(**train_cfg)['train']
    train_markups={micro.render(row['state']) for row in micro.training_rows(train)}
    train_shapes={_signature(c['state']) for c in train}
    fingerprints=set()
    for case in cases:
        if case['suite']!='control':
            if micro.render(case['state']) in train_markups:
                raise RuntimeError('MICRO_OOD_TRAIN_STATE_OVERLAP')
            if case['signature'] in train_shapes:
                raise RuntimeError('MICRO_OOD_TRAIN_SHAPE_OVERLAP')

        if case['suite']=='direction':continue
        key=micro.render(case['state'])
        assert key not in fingerprints,case['id']
        fingerprints.add(key)
    return cases


def select_cases(cfg,suites,limit_per_suite=None,seed=20261027):
    cases=generate_ood_cases(cfg,seed)
    selection=[]
    for suite in suites:
        group=[c for c in cases if c['suite']==suite]
        if limit_per_suite is not None:group=group[:limit_per_suite]
        selection.extend(group)
    return selection,cases


def cases_digest(cases):
    return base.digest([{'id':c['id'],'suite':c['suite'],
         'markup':micro.render(c['state']),'operations':c['operations'],
         'answer':c['answer']} for c in cases])


def preview(args,cfg):
    cases,all_cases=select_cases(cfg,args.suites,args.limit_per_suite,args.seed)
    examples=[{'id':c['id'],'suite':c['suite'],'operations':c['operations'],
       'scale':c['scale'],'answer':c['answer'],'max_literal':c['max_literal'],
       'negative_intermediate':c['negative_intermediate'],'state':micro.render(c['state'])}
       for c in cases[:args.preview_cases]]
    result={'schema':SCHEMA,'model_loaded':False,'checkpoint_loaded':False,
        'suites':args.suites,'total_cases':len(cases),'full_suite_cases':len(all_cases),
        'ood_deck_sha256':cases_digest(cases),
        'training_overlap_checked':True,
        'cases_per_suite':{s:sum(c['suite']==s for c in cases) for s in args.suites},
        'operations_per_suite':{s:sum(c['operations'] for c in cases if c['suite']==s) for s in args.suites},
        'max_generation_calls':2*sum(c['operations'] for c in cases)*len(args.controls),
        'examples':examples,'test_evaluated':False}
    print('MICRO_OOD_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)
    return result


def probe(args,cfg):
    import torch
    from tools import nanojev_qwen_hybridagent_activation as activation
    ck=Path(args.checkpoint)
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('MICRO_OOD_OUTPUT_NOT_EMPTY')
    if out.resolve()==ck.parent.resolve() or ck.parent.resolve() in out.resolve().parents or out.resolve() in ck.parent.resolve().parents:
        raise RuntimeError('MICRO_OOD_OUTPUT_OVERLAPS_CHECKPOINT')
    cases,all_cases=select_cases(cfg,args.suites,args.limit_per_suite,args.seed)
    if not cases:raise ValueError('MICRO_OOD_NO_CASES')
    model,tok,hashes,frozen,_=micro._load(args,checkpoint=ck)
    if hashes!=cfg['sources'] or frozen!=cfg['frozen_backbone']:
        raise RuntimeError('MICRO_OOD_SOURCE_DRIFT')
    model.backbone.eval();model.head.eval()
    records=[];chains=[];calls=0
    def perform(state,control,case,mode):
        nonlocal calls
        if calls==0 or (calls+1)%args.progress_every==0:
            print('MICRO_OOD_CALL='+json.dumps({'number':calls+1,'case':case['id'],
              'suite':case['suite'],'control':control,'mode':mode,'remaining':micro.steps(state)},sort_keys=True),flush=True)
        generated=base._decode(model,tok,micro.prompt(state),control,args.max_new_tokens,stop_at_end_rung=True)
        calls+=1
        if calls%args.progress_every==0:
            print('MICRO_OOD_PROGRESS='+json.dumps({'completed':calls,'case':case['id'],
               'mode':mode,'suite':case['suite']},sort_keys=True),flush=True)
        score=micro.evaluate_transition(state,generated)
        return generated,score
    print('MICRO_OOD_BEGIN='+json.dumps({'cases':len(cases),'controls':args.controls,
      'max_calls':2*sum(c['operations'] for c in cases)*len(args.controls),
      'suites':args.suites,'model_weights_modified':False,'test_evaluated':False},sort_keys=True),flush=True)
    with torch.inference_mode():
        for case in cases:
            for control in args.controls:
                state=case['state'];position=0
                while micro.steps(state)>0:
                    generated,score=perform(state,control,case,'teacher_state')
                    records.append({'case':case['id'],'suite':case['suite'],
                        'control':control,'operation_index':position,
                        'operations_total':case['operations'],'scale':case['scale'],
                        'answer_sign':(case['answer']>0)-(case['answer']<0),
                        'correct':score['valid'],'reason':score['reason'],
                        'input':micro.render(state),'generated':generated,
                        'teacher_target':micro.target(state)})
                    state=micro.reduce_one(state)
                    position+=1
                state=case['state'];trace=[];valid=True
                while micro.steps(state)>0:
                    generated,score=perform(state,control,case,'free_chain')
                    trace.append({'remaining':micro.steps(state),'correct':score['valid'],
                        'reason':score['reason'],'generated':generated})
                    if not score['valid']:
                        valid=False;break
                    state=score['state']
                answer_preserved=valid and micro.result_value(state)==case['answer']
                chains.append({'case':case['id'],'suite':case['suite'],'control':control,
                    'operations':case['operations'],'scale':case['scale'],
                    'answer':case['answer'],'reverse':case['reverse'],
                    'success':valid and answer_preserved,'steps_done':len(trace),
                    'trace':trace,'terminal':micro.render(state) if valid else None,
                    'answer_preserved':answer_preserved})
    result={'schema':SCHEMA,'status':'OOD_DIAGNOSTIC_NOT_CERTIFICATION',
      'checkpoint_sha256':activation.file_hash(ck),'source_deck_sha256':cfg['deck_hash'],
      'ood_deck_sha256':cases_digest(cases),'training_overlap_checked':True,
      'suites':args.suites,'controls':args.controls,'selected_cases':len(cases),
      'full_suite':args.limit_per_suite is None,'generation_calls':calls,
      'test_evaluated':False,'optimizer_steps':0,
      'oracle_intermediate_at_inference':False,'domain_words_in_micro_prompts':False,
      'records':len(records),'chains':len(chains),'score_by_suite':{},'score_by_depth':{},
      'score_by_scale':{},'failure_examples':[]}
    def scored(group):
        return {'one_step':{'correct':sum(r['correct'] for r in group[0]),'total':len(group[0]),
                'accuracy':round(sum(r['correct'] for r in group[0])/len(group[0]),5) if group[0] else None},
            'chains':{'correct':sum(c['success'] for c in group[1]),'total':len(group[1]),
                'accuracy':round(sum(c['success'] for c in group[1])/len(group[1]),5) if group[1] else None}}
    for control in args.controls:
        result['score_by_suite'][control]={}
        result['score_by_depth'][control]={}
        result['score_by_scale'][control]={}
        for suite in args.suites:
            result['score_by_suite'][control][suite]=scored((
                [r for r in records if r['control']==control and r['suite']==suite],
                [c for c in chains if c['control']==control and c['suite']==suite]))
        for depth in sorted(set(c['operations'] for c in cases)):
            ids={c['id'] for c in cases if c['operations']==depth}
            result['score_by_depth'][control][str(depth)]=scored((
                [r for r in records if r['control']==control and r['case'] in ids],
                [c for c in chains if c['control']==control and c['case'] in ids]))
        for scale in sorted(set(c['scale'] for c in cases)):
            ids={c['id'] for c in cases if c['scale']==scale}
            result['score_by_scale'][control][str(scale)]=scored((
                [r for r in records if r['control']==control and r['case'] in ids],
                [c for c in chains if c['control']==control and c['case'] in ids]))
    result['failure_examples']=[{'case':r['case'],'suite':r['suite'],'control':r['control'],
        'operation_index':r['operation_index'],'reason':r['reason']}
        for r in records if not r['correct']][:15]
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'result.json',result)
    base._atomic_json(out/'one_steps.json',{'records':records})
    base._atomic_json(out/'free_chains.json',{'records':chains})
    base._atomic_json(out/'cases.json',{'cases':[
        {k:v for k,v in c.items() if k not in ('state','signature')}|{'state':micro.render(c['state'])}
         for c in cases]})
    print('MICRO_OOD_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','probe'),default='preview')
    p.add_argument('--checkpoint',required=True,help='Existing micro TEXT or LINK best.pt (read-only)')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_micro_ood_v1'))
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--suites',default=','.join(SUITES))
    p.add_argument('--seed',type=int,default=20261027)
    p.add_argument('--controls',default='text_only')
    p.add_argument('--limit-per-suite',type=int,help='Smoke only; omit for complete OOD deck')
    p.add_argument('--preview-cases',type=int,default=6)
    p.add_argument('--max-new-tokens',type=int,default=512)
    p.add_argument('--progress-every',type=int,default=10)
    p.add_argument('--baseline');p.add_argument('--backbone')
    p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    args.suites=[s.strip() for s in args.suites.split(',') if s.strip()]
    if len(set(args.suites))!=len(args.suites) or not args.suites or any(s not in SUITES for s in args.suites):
        p.error('MICRO_OOD_BAD_SUITES')
    args.controls=[s.strip() for s in args.controls.split(',') if s.strip()]
    if not args.controls or any(s not in ('text_only','zero_latent','intact') for s in args.controls):
        p.error('MICRO_OOD_BAD_CONTROLS')
    if args.limit_per_suite is not None and args.limit_per_suite<1:p.error('MICRO_OOD_BAD_LIMIT')
    if args.max_new_tokens<16:p.error('MICRO_OOD_BAD_GENERATION_LIMIT')
    if args.progress_every<1:p.error('MICRO_OOD_BAD_PROGRESS_INTERVAL')
    ck=Path(args.checkpoint)
    if not ck.is_file():p.error('MICRO_OOD_NEEDS_EXISTING_CHECKPOINT')
    try:cfg=json.loads((ck.parent/'run_config.json').read_text(encoding='utf8'))
    except (OSError,ValueError) as exc:p.error(f'MICRO_OOD_MISSING_RUN_CONFIG: {exc}')
    if cfg.get('schema')!=micro.SCHEMA:p.error('MICRO_OOD_WRONG_CHECKPOINT_FAMILY')
    if args.stage=='preview':preview(args,cfg)
    else:probe(args,cfg)


if __name__=='__main__':main()
