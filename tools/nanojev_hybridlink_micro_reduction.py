#!/usr/bin/env python3
"""Anonymous, one-arithmetic-operation-per-invocation B0b->B0a experiment.

No semantic vocabulary is given to the low-level model.  Independent Python
arithmetic generates labels and validates output; it never runs as an inference
executor or corrects a failed autonomous chain.  Not QW5 Stage-2 certification.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import copy
import json
from pathlib import Path
import random
import re

from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA='nanojev.hybridlink.anonymous-micro-reduction.v1'
REGISTERS=(('A','B','R'),('X','Y','Z'),('U','V','W'),('M','N','Q'))
OPS=(ast.Add,ast.Sub,ast.Mult)
END='END_RUNG'


def _node(expr):
    tree=ast.parse(expr,mode='eval').body
    def check(n):
        if isinstance(n,ast.Constant) and type(n.value) is int:return
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub) and isinstance(n.operand,ast.Constant) and type(n.operand.value) is int:return check(n.operand)
        if isinstance(n,ast.BinOp) and isinstance(n.op,OPS):
            check(n.left);check(n.right);return
        raise ValueError('MICRO_UNSAFE_ARITHMETIC')
    check(tree)
    return tree


def _value(node):
    if isinstance(node,ast.Constant):return node.value
    if isinstance(node,ast.UnaryOp):return -_value(node.operand)
    x,y=_value(node.left),_value(node.right)
    if isinstance(node.op,ast.Add):return x+y
    if isinstance(node.op,ast.Sub):return x-y
    if isinstance(node.op,ast.Mult):return x*y
    raise ValueError('MICRO_UNSAFE_OPERATOR')


def _render(n):
    if isinstance(n,ast.Constant):return str(n.value)
    if isinstance(n,ast.UnaryOp):return '-'+_render(n.operand)
    op=' + ' if isinstance(n.op,ast.Add) else ' - ' if isinstance(n.op,ast.Sub) else ' * '
    def part(ch):
        s=_render(ch)
        if isinstance(n.op,(ast.Add,ast.Sub)) and isinstance(ch,ast.BinOp):return '('+s+')'
        if isinstance(n.op,ast.Mult) and isinstance(ch,ast.BinOp):return '('+s+')'
        return s
    return part(n.left)+op+part(n.right)


def _step_nodes(n):
    """All legal one-op reductions. A node is reducible only when its operands are literals."""
    if not isinstance(n,ast.BinOp):return []
    if not isinstance(n.left,ast.BinOp) and not isinstance(n.right,ast.BinOp):
        return [ast.Constant(_value(n))]
    out=[]
    for field in ('left','right'):
        child=getattr(n,field)
        for reduced in _step_nodes(child):
            clone=copy.deepcopy(n)
            setattr(clone,field,reduced)
            out.append(clone)
    return out


def _count_ops(n):
    if isinstance(n,ast.BinOp):return 1+_count_ops(n.left)+_count_ops(n.right)
    return 0


def _state(names,a_expr,b_expr,reverse=False):
    a,b,r=names
    return {'names':tuple(names),'a':_node(a_expr),'b':_node(b_expr),'reverse':bool(reverse)}


def render(s):
    a,b,r=s['names']
    direction=f'{b} - {a}' if s['reverse'] else f'{a} - {b}'
    return f'<REDUCE>\n{a} = {_render(s["a"])}\n{b} = {_render(s["b"])}\n{r} = {direction}\n</REDUCE>'


def parse(text, *, terminal=False):
    """No arbitrary code, no domain words, exactly three anonymous assignments."""
    payload=text.strip()
    if terminal:
        if not payload.endswith('\n'+END):raise ValueError('MICRO_MISSING_TERMINATOR')
        payload=payload[:-(len(END)+1)].strip()
    lines=payload.splitlines()
    if len(lines)!=5 or lines[0]!='<REDUCE>' or lines[-1]!='</REDUCE>':
        raise ValueError('MICRO_INVALID_MARKUP')
    a,b,r=[re.fullmatch(r'([A-Z]) = (.+)',line) for line in lines[1:4]]
    if any(x is None for x in (a,b,r)):
        raise ValueError('MICRO_INVALID_ASSIGNMENT')
    names=(a.group(1),b.group(1),r.group(1))
    if len(set(names))!=3:raise ValueError('MICRO_DUPLICATE_REGISTERS')
    direction=r.group(2)
    if direction==f'{names[0]} - {names[1]}':reverse=False
    elif direction==f'{names[1]} - {names[0]}':reverse=True
    else:raise ValueError('MICRO_DIRECTION_CHANGED')
    return _state(names,a.group(2),b.group(2),reverse)


def _canonical(n):
    """Canonicalize Python's AST representation of signed integer literals.

    The model emits '-7', which Python parses as UnaryOp(USub, Constant(7)),
    while our arithmetic teacher may hold Constant(-7).  Treat them alike
    without accepting a changed operation or changed sign.
    """
    if isinstance(n,ast.Constant) and type(n.value) is int:
        return ('integer',n.value)
    if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub) and isinstance(n.operand,ast.Constant) and type(n.operand.value) is int:
        return ('integer',-n.operand.value)
    if isinstance(n,ast.BinOp) and isinstance(n.op,OPS):
        return (type(n.op).__name__,_canonical(n.left),_canonical(n.right))
    raise ValueError('MICRO_UNSAFE_ARITHMETIC')


def _same(a,b):
    return _canonical(a)==_canonical(b)


def successors(s):
    out=[]
    for field in ('a','b'):
        for node in _step_nodes(s[field]):
            altered={**s,field:node}
            out.append(altered)
    return out


def reduce_one(s):
    available=successors(s)
    return available[0] if available else None


def steps(s):return _count_ops(s['a'])+_count_ops(s['b'])


def result_value(s):
    x,y=_value(s['a']),_value(s['b'])
    return y-x if s['reverse'] else x-y


def evaluate_transition(before,output):
    try:after=parse(output,terminal=True)
    except (ValueError,SyntaxError,OverflowError) as e:
        return {'valid':False,'reason':str(e),'state':None}
    if before['names']!=after['names'] or before['reverse']!=after['reverse']:
        return {'valid':False,'reason':'register_or_direction_drift','state':None}
    if not any(_same(after['a'],s['a']) and _same(after['b'],s['b']) for s in successors(before)):
        return {'valid':False,'reason':'not_exactly_one_correct_operation','state':None}
    if steps(after)!=steps(before)-1 or result_value(after)!=result_value(before):
        return {'valid':False,'reason':'invariant_violation','state':None}
    return {'valid':True,'reason':None,'state':after}


def prompt(s):
    return ('Reduce exactly one arithmetic operation. Keep every other operation, '
            'register, and the subtraction direction unchanged.\n'
            'Return only the updated <REDUCE> block, followed by END_RUNG.\n'
            'Current:\n'+render(s)+'\nNext:\n')


def target(s):
    following=reduce_one(s)
    if following is None:raise ValueError('MICRO_ALREADY_REDUCED')
    return render(following)+'\n'+END+'\n'


def _scenario(chain,i):
    names=REGISTERS[i%len(REGISTERS)]
    a,b=chain['a'],chain['b']
    k=chain['variant']+1
    # Same +/- residual invariant as the source deck, but no semantic nouns,
    # labels, or source-domain information in micro prompts/targets.
    first=f'({a} * {k+1}) + ({b} * {k})'
    second=f'({a} * {k}) + ({b} * {k+1})'
    # Real variety in tree shape, not just variable renaming.
    if i%3==1:first=f'({b} * {k}) + ({a} * {k+1})'
    if i%3==2:second=f'({b} * {k+1}) + ({a} * {k})'
    return _state(names,first,second,chain['reverse'])


def deck_for(*,band,scales,seed,train_count,dev_count,test_count):
    source=base.generate_deck(band=band,scales=scales,seed=seed,
         train_count=train_count,dev_count=dev_count,test_count=test_count)
    deck={sp:[{'id':ch['case_id'],'state':_scenario(ch,int(ch['case_id'].split('-')[-1])),
               'key':base.case_key(ch),'delta':ch['delta'],'scale':ch['a'],
               'answer':ch['answer']} for ch in source[sp]] for sp in source}
    return deck


def training_rows(deck):
    rows=[]
    for case in deck:
        state=case['state']
        while steps(state):
            rows.append({'id':case['id'],'position':steps(case['state'])-steps(state),
                         'prompt':prompt(state),'target':target(state),'state':state,
                         'expected':case['answer']})
            state=reduce_one(state)
        assert result_value(state)==case['answer']
    return rows


def deck_hash(deck):
    return base.digest({sp:[{'id':x['id'],'key':x['key'],'markup':render(x['state']),
            'answer':x['answer']} for x in rows] for sp,rows in deck.items()})


def _load(args,checkpoint=None,warm_start=None):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    model,tok,hashes,frozen,dirs=base._load(args,trained=Path(warm_start) if warm_start else None)
    if checkpoint:
        ck=torch.load(checkpoint,weights_only=True,map_location='cpu')
        if ck.get('schema')!=SCHEMA or ck.get('status') not in ('MICRO_TEXT','MICRO_LINK'):
            raise RuntimeError('MICRO_CHECKPOINT_SCHEMA')
        if ck['source_sha256']!=hashes or ck['frozen_backbone_sha256']!=frozen:
            raise RuntimeError('MICRO_SOURCE_HASH_DRIFT')
        saved_config=json.loads((Path(checkpoint).parent/'run_config.json').read_text(encoding='utf8'))
        if ck['config_hash']!=base.digest(saved_config):
            raise RuntimeError('MICRO_CHECKPOINT_CONFIG_DRIFT')
        zero.restore_lora(model.backbone,ck['lora'])
        model.head.load_state_dict(ck['head'],strict=True)
    return model,tok,hashes,frozen,dirs


def _config_args(args):
    return dict(band=tuple(args.band),scales=args.scales,seed=args.seed,
      train_count=args.train_count,dev_count=args.dev_count,test_count=args.test_count)


def _balanced_dev(rows,limit):
    # Every operation position and sign must contribute before a position repeats.
    rng=random.Random(123)
    buckets=defaultdict(list)
    for row in rows:buckets[(row['position'],0 if row['expected']==0 else (1 if row['expected']>0 else -1))].append(row)
    for bucket in buckets.values():rng.shuffle(bucket)
    keys=sorted(buckets)
    selection=[]
    while len(selection)<min(limit,len(rows)):
        advanced=False
        for k in keys:
            if buckets[k]:selection.append(buckets[k].pop());advanced=True
            if len(selection)==min(limit,len(rows)):break
        if not advanced:break
    return selection


def train(args,kind):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    if args.steps<1 or args.eval_every<1 or args.lr<=0 or args.max_seq<128:
        raise ValueError('MICRO_BAD_TRAIN_PARAMETERS')
    if kind=='link' and not args.text_checkpoint:
        raise ValueError('MICRO_LINK_REQUIRES_TEXT_CHECKPOINT')
    deck=deck_for(**_config_args(args));rows=training_rows(deck['train'])
    dev=_balanced_dev(training_rows(deck['dev']),args.dev_eval_samples)
    parent=Path(args.text_checkpoint) if kind=='link' else None
    if parent:
        cfg=json.loads((parent.parent/'run_config.json').read_text())
        if cfg['schema']!=SCHEMA or cfg['stage']!='train-text' or cfg['deck_hash']!=deck_hash(deck):
            raise RuntimeError('MICRO_PARENT_DATASET_DRIFT')
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('MICRO_OUTPUT_NOT_EMPTY')
    model,tok,hashes,frozen,dirs=_load(args,checkpoint=parent if kind=='link' else None,
                                      warm_start=args.warm_start if kind=='text' else None)
    if any(out.resolve()==d.resolve() or out.resolve() in d.resolve().parents or
           d.resolve() in out.resolve().parents for d in dirs):raise RuntimeError('MICRO_FROZEN_SOURCE_OVERLAP')
    model.backbone.requires_grad_(False)
    for _,p in zero.lora_named_parameters(model.backbone):p.requires_grad_(kind=='text')
    model.head.requires_grad_(kind=='link')
    params=([p for _,p in zero.lora_named_parameters(model.backbone)] if kind=='text'
            else list(model.head.parameters()))
    before_head={k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()} if kind=='text' else None
    before_lora=zero.lora_state(model.backbone) if kind=='link' else None
    objective=base.text_loss if kind=='text' else base.latent_loss
    config={'schema':SCHEMA,'stage':'train-'+kind,'sources':hashes,'frozen_backbone':frozen,
            **{k:list(v) if isinstance(v,tuple) else v for k,v in _config_args(args).items()},
            'deck_hash':deck_hash(deck),'steps':args.steps,'eval_every':args.eval_every,
            'lr':args.lr,'max_seq':args.max_seq,'train_hops':len(rows),'dev_hops':len(training_rows(deck['dev'])),
            'dev_eval_samples':len(dev),'text_parent_sha256':activation.file_hash(parent) if parent else None,
            'warm_start_sha256':activation.file_hash(Path(args.warm_start)) if args.warm_start and kind=='text' else None}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'run_config.json',config)
    rng=random.Random(args.seed+903);rng.shuffle(rows)
    opt=torch.optim.AdamW(params,lr=args.lr,weight_decay=0)
    best=float('inf');best_step=None
    torch.manual_seed(args.seed)
    for step in range(1,args.steps+1):
        row=rows[(step-1)%len(rows)]
        model.backbone.eval();model.head.eval()
        opt.zero_grad(set_to_none=True)
        loss=objective(model,tok,row,args.device,args.max_seq)
        if not bool(torch.isfinite(loss).all()):raise RuntimeError('MICRO_NONFINITE_LOSS')
        loss.backward();torch.nn.utils.clip_grad_norm_(params,1);opt.step()
        if step%args.eval_every and step!=args.steps:continue
        with torch.no_grad():
            dev_loss=sum(float(objective(model,tok,x,args.device,args.max_seq)) for x in dev)/len(dev)
        if dev_loss<best:
            best=dev_loss;best_step=step
            base._atomic_torch(out/'best.pt',{
              'schema':SCHEMA,'status':'MICRO_TEXT' if kind=='text' else 'MICRO_LINK',
              'source_sha256':hashes,'frozen_backbone_sha256':frozen,
              'config_hash':base.digest(config),'step':step,'dev_loss':dev_loss,
              'lora':zero.lora_state(model.backbone),
              'head':{k:v.detach().cpu().clone() for k,v in model.head.state_dict().items()}})
        print('MICRO_TRAIN_PROGRESS='+json.dumps({'step':step,'stage':kind,'train_loss':round(float(loss.detach()),5),
              'dev_teacher_forced_loss':round(dev_loss,5),'best_dev_loss':round(best,5)},sort_keys=True),flush=True)
    if zero.frozen_backbone_sha256(model.backbone)!=frozen:raise RuntimeError('MICRO_FROZEN_BACKBONE_MUTATED')
    if before_head and any(not torch.equal(model.head.state_dict()[k].detach().cpu(),v) for k,v in before_head.items()):
        raise RuntimeError('MICRO_TEXT_MODIFIED_HEAD')
    if before_lora and any(not torch.equal(dict(zero.lora_named_parameters(model.backbone))[k].detach().cpu(),v) for k,v in before_lora.items()):
        raise RuntimeError('MICRO_LINK_MODIFIED_LORA')
    result={'schema':SCHEMA,'status':'TRAINED_UNCERTIFIED','stage':'train-'+kind,
            'best_step':best_step,'dev_teacher_forced_loss':best,'test_evaluated':False}
    base._atomic_json(out/'result.json',result)
    print('MICRO_TRAIN_RESULT='+json.dumps(result,sort_keys=True),flush=True)


def probe(args):
    import torch
    from tools import nanojev_qwen_hybridagent_activation as activation
    ck=Path(args.checkpoint)
    if not ck.is_file():raise ValueError('MICRO_NEEDS_CHECKPOINT')
    cfg=json.loads((ck.parent/'run_config.json').read_text())
    if cfg['schema']!=SCHEMA:raise ValueError('MICRO_WRONG_CONFIG')
    options=dict(band=tuple(cfg['band']),scales=tuple(cfg['scales']),seed=cfg['seed'],
         train_count=cfg['train_count'],dev_count=cfg['dev_count'],test_count=cfg['test_count'])
    deck=deck_for(**options)
    if deck_hash(deck)!=cfg['deck_hash']:raise RuntimeError('MICRO_DATASET_DRIFT')
    all_cases=deck[args.split]
    if args.case_limit is not None and (args.case_limit<1 or args.case_limit>len(all_cases)):
        raise ValueError('MICRO_BAD_CASE_LIMIT')
    if args.split=='test' and args.case_limit is not None:raise RuntimeError('MICRO_TEST_REQUIRES_COMPLETE_SPLIT')
    if args.split=='test' and (ck.parent/'test_consumed.json').exists():raise RuntimeError('MICRO_TEST_ALREADY_CONSUMED')
    cases=all_cases[:args.case_limit] if args.case_limit is not None else all_cases
    out=Path(args.output)
    if out.exists() and any(out.iterdir()):raise RuntimeError('MICRO_OUTPUT_NOT_EMPTY')
    if out.resolve()==ck.parent.resolve() or out.resolve() in ck.parent.resolve().parents or ck.parent.resolve() in out.resolve().parents:
        raise RuntimeError('MICRO_OUTPUT_OVERLAPS_CHECKPOINT')
    model,tok,hashes,frozen,dirs=_load(args,checkpoint=ck)
    if hashes!=cfg['sources'] or frozen!=cfg['frozen_backbone']:
        raise RuntimeError('MICRO_SOURCE_DRIFT')
    model.backbone.eval();model.head.eval()
    records=[];chains=[];total=0
    print('MICRO_PROBE_BEGIN='+json.dumps({'cases':len(cases),'controls':args.controls,
          'up_to_calls':len(cases)*len(args.controls)*12,'split':args.split,
          'subset':len(cases)!=len(all_cases)},sort_keys=True),flush=True)
    def call(state,control,case_id,kind):
        nonlocal total
        print('MICRO_PROBE_CALL='+json.dumps({'number':total+1,'case':case_id,'kind':kind,
              'control':control,'remaining_operations':steps(state)},sort_keys=True),flush=True)
        completion=base._decode(model,tok,prompt(state),control,args.max_new_tokens,stop_at_end_rung=True)
        total+=1
        scored=evaluate_transition(state,completion)
        return completion,scored
    with torch.inference_mode():
        for case in cases:
            for control in args.controls:
                # Separate teacher-state diagnostics: NOT inputs to the free-running chain.
                state=case['state']
                while steps(state):
                    completion,score=call(state,control,case['id'],'one_step')
                    records.append({'case_id':case['id'],'control':control,'mode':'teacher_input',
                         'position':steps(case['state'])-steps(state),'correct':score['valid'],
                         'reason':score['reason'],'expected':target(state),'generated':completion})
                    state=reduce_one(state)
                # Free-running: the model's own exact output feeds its next step.
                state=case['state'];passed=True;visited=[]
                while steps(state):
                    completion,score=call(state,control,case['id'],'free_chain')
                    visited.append({'remaining_before':steps(state),'correct':score['valid'],
                        'reason':score['reason'],'generated':completion})
                    if not score['valid']:
                        passed=False;break
                    state=score['state']
                chains.append({'case_id':case['id'],'control':control,'success':passed,
                    'steps_done':len(visited),'trace':visited,'terminal':render(state) if passed else None,
                    'answer_preserved':passed and result_value(state)==case['answer']})
    by_control={}
    for control in args.controls:
        a=[x for x in records if x['control']==control]
        c=[x for x in chains if x['control']==control]
        by_control[control]={'one_step_correct':sum(x['correct'] for x in a),'one_step_total':len(a),
            'one_step_accuracy':round(sum(x['correct'] for x in a)/len(a),5),
            'chains_correct':sum(x['success'] and x['answer_preserved'] for x in c),'chains_total':len(c)}
    result={'schema':SCHEMA,'status':'PROBE_UNCERTIFIED','split':args.split,'subset':len(cases)!=len(all_cases),
            'case_count':len(cases),'generation_calls':total,'controls':by_control,
            'checkpoint_sha256':activation.file_hash(ck),'test_evaluated':args.split=='test',
            'oracle_intermediate_at_inference':False,'domain_words_in_micro_prompts':False}
    out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'result.json',result)
    base._atomic_json(out/'records.json',{'one_hops':records,'free_chains':chains})
    if args.split=='test':
        base._atomic_json(ck.parent/'test_consumed.json',{'schema':SCHEMA,'run':str(out),'sha256':result['checkpoint_sha256']})
    print('MICRO_PROBE_RESULT='+json.dumps(result,sort_keys=True),flush=True)


def preview(args):
    deck=deck_for(**_config_args(args))
    sample=next(x for x in deck['train'] if x['delta']!=0)
    state=sample['state'];states=[render(state)]
    while steps(state):
        state=reduce_one(state);states.append(render(state))
    assert all('Hawk' not in x and 'Dove' not in x for x in states)
    result={'schema':SCHEMA,'model_loaded':False,'scenario_counts':{k:len(v) for k,v in deck.items()},
       'micro_rows':{k:len(training_rows(v)) for k,v in deck.items()},
       'example_ladder':states,'answer':sample['answer'],'deck_sha256':deck_hash(deck)}
    print('MICRO_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train-text','train-link','probe'),default='preview')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--band',default='0-4')
    p.add_argument('--scales',default='8,16,32,64,128,256')
    p.add_argument('--seed',type=int,default=71)
    p.add_argument('--train-count',type=int,default=36)
    p.add_argument('--dev-count',type=int,default=12)
    p.add_argument('--test-count',type=int,default=12)
    p.add_argument('--steps',type=int,default=600)
    p.add_argument('--eval-every',type=int,default=50)
    p.add_argument('--dev-eval-samples',type=int,default=24)
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--max-seq',type=int,default=512)
    p.add_argument('--max-new-tokens',type=int,default=128)
    p.add_argument('--baseline');p.add_argument('--backbone')
    p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--warm-start',help='Optional existing coarse rung text best.pt, training text only')
    p.add_argument('--text-checkpoint',help='Micro text best.pt, required for train-link')
    p.add_argument('--checkpoint',help='Micro text/link best.pt, required for probe')
    p.add_argument('--controls',default='text_only')
    p.add_argument('--split',choices=('dev','test'),default='dev')
    p.add_argument('--case-limit',type=int,help='DEV smoke only')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_micro_v1'))
    args=p.parse_args(argv)
    try:
        a,b=args.band.split('-',1);args.band=(int(a),int(b))
        args.scales=base.scalelist(args.scales)
    except ValueError as e:p.error(str(e))
    args.controls=[x.strip() for x in args.controls.split(',') if x.strip()]
    if not args.controls or any(x not in ('text_only','zero_latent','intact') for x in args.controls):
        p.error('Invalid controls')
    if args.dev_eval_samples<6:p.error('--dev-eval-samples must be >=6')
    if args.stage=='preview':preview(args)
    elif args.stage=='probe':probe(args)
    elif args.stage=='train-text':train(args,'text')
    elif args.stage=='train-link':train(args,'link')


if __name__=='__main__':main()
