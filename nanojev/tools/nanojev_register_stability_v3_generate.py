#!/usr/bin/env python3
"""Generate and optionally capture the frozen-Qwen XXXXXX/YYYYYY register v3 experiment.

Default budget: 2560 main ordered pairs x 8 prompts, plus 128 held-out control
ordered pairs x all 24 operations = exactly 23,552 unique prompt instances.
No LASSO coefficients or fixed x_i coordinates are imported. The future v3
analyzer should learn fresh heads and keep numerical-pair groups disjoint.

Generation requires Python stdlib only. --capture-only additionally uses the
existing NanoJev activation cassette stack installed beside this script.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import time

SCHEMA = 'nanojev.register-stability-v3.generator.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'register_stability_v3'
FIRST, SECOND = 'XXXXXX', 'YYYYYY'
MAX_VALUE = 2_000_000
# Each operation is (name, family, instruction). No numeric operand appears
# outside the registration prefix; constants used by an instruction are fine.
OPERATIONS = (
    ('sym_add','symbolic',f'{FIRST} + {SECOND} ='),
    ('sym_sub','symbolic',f'{FIRST} - {SECOND} ='),
    ('sym_reverse_sub','symbolic',f'{SECOND} - {FIRST} ='),
    ('sym_multiply','symbolic',f'{FIRST} * {SECOND} ='),
    ('sym_absdiff','symbolic',f'abs({FIRST} - {SECOND}) ='),
    ('sym_max','symbolic',f'max({FIRST}, {SECOND}) ='),
    ('written_add','written',f'Calculate the sum of {FIRST} and {SECOND}.'),
    ('written_sub','written',f'Subtract {SECOND} from {FIRST}.'),
    ('written_product','written',f'What is the product of {FIRST} and {SECOND}?'),
    ('written_difference','written',f'Find the difference between {SECOND} and {FIRST}.'),
    ('written_greater','written',f'In words, is {FIRST} greater than {SECOND}?'),
    ('written_average','written',f'What is the arithmetic mean of {FIRST} and {SECOND}?'),
    ('algebra_left','algebra',f'Solve for Z: Z + {SECOND} = {FIRST}.'),
    ('algebra_right','algebra',f'Solve for Z: {FIRST} + Z = {SECOND}.'),
    ('algebra_difference','algebra',f'Find Z if Z - {FIRST} = {SECOND}.'),
    ('algebra_balance','algebra',f'Solve Z in the equation {FIRST} - Z = {SECOND}.'),
    ('conditional_gt','conditional',f'Is {FIRST} > {SECOND}? Answer true or false.'),
    ('conditional_min','conditional',f'Return the smaller of {FIRST} and {SECOND}.'),
    ('conditional_positive','conditional',f'If {FIRST} is positive, return {SECOND}; otherwise return {FIRST}.'),
    ('conditional_abs','conditional',f'Which register has greater absolute magnitude, {FIRST} or {SECOND}?'),
    ('multistep_double','multistep',f'Double {FIRST}, then subtract {SECOND}.'),
    ('multistep_combine','multistep',f'Add {FIRST} to {SECOND}, then double that result.'),
    ('multistep_square','multistep',f'Add {FIRST} and {SECOND}; square the resulting sum.'),
    ('multistep_difference','multistep',f'Compute {FIRST} minus {SECOND}, then add {FIRST}.'),
)
# Entire instruction variants absent from main training. All are present in
# the held-out full-operation control, enabling genuine operation transfer.
HELD_OUT_OPERATIONS = ('written_average', 'algebra_balance',
                       'conditional_abs', 'multistep_square')
FAMILIES = ('symbolic','written','algebra','conditional','multistep')


def emit(event, **values):
    print(json.dumps({'event':event, **values},sort_keys=True), flush=True)


def positives():
    """Match the original NanoJev multiscale signed ±2M numeric grid."""
    bands = [list(range(1,21)),list(range(25,201,5)),
             list(range(250,2001,50)),list(range(2500,20001,500)),
             list(range(25000,200001,5000))]
    tail = [(220_000*(88-i)+MAX_VALUE*i+44)//88 for i in range(89)]
    vals = [v for band in bands for v in band]+tail
    assert len(vals)==253 and vals==sorted(set(vals)) and vals[-1]==MAX_VALUE
    return vals


def numeric_grid():
    p=positives()
    return [-n for n in reversed(p)]+[0]+p


def logbar(n):
    q=int(math.floor(math.log1p(abs(n))*10+0.5))
    major,remainder=divmod(q,10)
    return ('-' if n<0 else '')+'|'*major+'.'+'|'*remainder


def number_repr(n):
    return f'{n}, {logbar(n)}'


def render(a,b,op):
    return (f'{FIRST} = {number_repr(a)}\n'
            f'{SECOND} = {number_repr(b)}\n'
            f'operation: {op[2]}\nAnswer:')


def group_bank(seed, non_equal=1328, equal=32):
    """Generate 1328 unordered non-equal pairs + 32 equality controls."""
    rng=random.Random(seed)
    grid=numeric_grid()
    bands=[tuple(n for n in grid if abs(n)<=20),
           tuple(n for n in grid if 20<abs(n)<=200),
           tuple(n for n in grid if 200<abs(n)<=2000),
           tuple(n for n in grid if 2000<abs(n)<=20000),
           tuple(n for n in grid if 20000<abs(n)<=200000),
           tuple(n for n in grid if abs(n)>200000)]
    anchor=[(2_000_000,-3),(-2_000_000,1),(0,2_000_000),(0,-2_000_000),
            (17,19),(-17,-19),(1,-1),(200,2000),(-200,2000),
            (2_000_000,-2_000_000),(-1,2_000_000)]
    groups=[];seen=set()
    def add(a,b):
        if a==b: return False
        canonical=(min(a,b),max(a,b))
        if canonical in seen: return False
        seen.add(canonical)
        groups.append(canonical)
        return True
    for a,b in anchor: add(a,b)
    shuffled=list(grid);rng.shuffle(shuffled)
    shuffled2=list(grid);rng.shuffle(shuffled2)
    for a,b in zip(shuffled,shuffled2): add(a,b)
    trials=0
    while len(groups)<non_equal:
        trials+=1
        if trials>non_equal*300:
            raise RuntimeError('REGISTER_V3_PAIR_GENERATION_EXHAUSTED')
        mode=trials%7
        if mode==0: band_a,band_b=0,5
        elif mode==1: band_a=band_b=rng.randrange(2,6)
        elif mode==2: band_a,band_b=0,rng.randrange(1,6)
        elif mode==3: band_a,band_b=rng.randrange(6),rng.randrange(6)
        elif mode==4: band_a,band_b=rng.randrange(1,5),rng.randrange(1,5)
        elif mode==5: band_a,band_b=5,0
        else: band_a=band_b=-1
        if band_a<0: a,b=rng.choice(grid),rng.choice(grid)
        else: a,b=rng.choice(bands[band_a]),rng.choice(bands[band_b])
        add(a,b)
    # Equality probes are genuine distinct (n,n) pairs, not synthetic duplicates.
    egal=[0,1,-1,2,-2,3,-3,17,-17,19,-19,20,-20,100,-100,
          200,-200,1000,-1000,2000,-2000,11000,-11000,20000,-20000,
          100000,-100000,200000,-200000,MAX_VALUE,-MAX_VALUE,25000]
    values=set(grid)
    assert equal==32 and len(egal)==equal and all(v in values for v in egal)
    rng.shuffle(groups);rng.shuffle(egal)
    return groups,egal


def assign_pairs(seed,main_pairs=2560,control_pairs=128):
    if main_pairs!=2560 or control_pairs!=128:
        raise ValueError('REGISTER_V3_FIXED_BUDGET_REQUIRES_2560_MAIN_128_CONTROL')
    groups,equal=group_bank(seed)
    # 1266 unequal groups *2 +28 equality =2560; 62*2+4=128.
    main_groups=groups[:1266];control_groups=groups[1266:]
    main_equal=equal[:28];control_equal=equal[28:]
    assert len(control_groups)==62
    rng=random.Random(seed+101)
    rows=[]
    for cohort,group_subset,equal_subset in (
        ('main',main_groups,main_equal),('control',control_groups,control_equal)):
        group_rows=[]
        for a,b in group_subset:
            key=f'pair:{a}:{b}'
            group_rows.extend([{'a':a,'b':b,'pair_group':key},
                               {'a':b,'b':a,'pair_group':key}])
        for n in equal_subset:
            group_rows.append({'a':n,'b':n,'pair_group':f'equal:{n}'})
        rng.shuffle(group_rows)
        for row in group_rows:
            row['cohort']=cohort
            # Group-based partition keeps reversed pairs in the same split.
            bucket=int(hashlib.sha256((str(seed)+'|'+row['pair_group']).encode()).hexdigest()[:8],16)%100
            row['split']=('control' if cohort=='control' else
                          'train' if bucket<80 else 'val' if bucket<90 else 'test')
            row['pair_id']=len(rows)
            rows.append(row)
    assert len(rows)==main_pairs+control_pairs
    assert len({(r['a'],r['b']) for r in rows})==len(rows)
    assert len({r['pair_group'] for r in rows if r['cohort']=='main'} &
               {r['pair_group'] for r in rows if r['cohort']=='control'})==0
    return rows


def choose_operations(pair_id, seed, count=8):
    # All training variants appear repeatedly; each pair sees all five
    # operation families. Select three additional families in a round-robin.
    rng=random.Random(seed*100_000+pair_id)
    available=[op for op in OPERATIONS if op[0] not in HELD_OUT_OPERATIONS]
    grouped={f:[op for op in available if op[1]==f] for f in FAMILIES}
    extra=[FAMILIES[(pair_id+j)%len(FAMILIES)] for j in range(3)]
    choices=[]
    for family in FAMILIES:
        need=1+extra.count(family)
        choices.extend(rng.sample(grouped[family],need))
    rng.shuffle(choices)
    assert len(choices)==count and len({c[0] for c in choices})==count
    return choices


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def build_plan(seed=20261013, max_prompts=24000):
    if max_prompts<23552:
        raise ValueError('REGISTER_V3_BUDGET_TOO_SMALL_FOR_FIXED_DESIGN')
    if max_prompts>24000:
        raise ValueError('REGISTER_V3_HARD_CEILING_IS_24000')
    pairs=assign_pairs(seed)
    tasks=[];used=set();operation_counts=Counter();family_counts=Counter()
    for pair in pairs:
        ops=choose_operations(pair['pair_id'],seed) if pair['cohort']=='main' else OPERATIONS
        for op in ops:
            text=render(pair['a'],pair['b'],op)
            key=digest(text)
            if key in used:
                raise RuntimeError('REGISTER_V3_DUPLICATE_PROMPT')
            used.add(key)
            tasks.append({'task_id':len(tasks), 'pair_id':pair['pair_id'],
                          'pair_group':pair['pair_group'], 'split':pair['split'],
                          'cohort':pair['cohort'],'a':pair['a'],'b':pair['b'],
                          'operation':op[0],'family':op[1],'sha256':key,'prompt':text})
            operation_counts[op[0]]+=1;family_counts[op[1]]+=1
    if len(tasks)!=23552 or len(tasks)>max_prompts:
        raise RuntimeError(f'REGISTER_V3_BUDGET_INVARIANT_FAILED:{len(tasks)}')
    return pairs,tasks,operation_counts,family_counts


def serialized_jsonl(rows):
    return ''.join(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for row in rows)


def write_atomic(path,content):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.partial')
    with tmp.open('w',encoding='utf-8',newline='\n') as file:
        file.write(content)
        file.flush();os.fsync(file.fileno())
    os.replace(tmp,path)


def setup(args, write=True):
    pairs,tasks,op_counts,fam_counts=build_plan(args.seed,args.max_prompts)
    pair_text=serialized_jsonl(pairs)
    task_text=serialized_jsonl(tasks)
    operations_doc=[{'name':a,'family':b,'instruction':c,
                     'held_out_operation':a in HELD_OUT_OPERATIONS} for a,b,c in OPERATIONS]
    manifest={
        'schema':SCHEMA,'seed':args.seed,'registration':{'first':FIRST,'second':SECOND,
            'syntax':'{name} = {decimal}, {logbar}\\n', 'representation':'paired'},
        'pair_count':len(pairs),'main_pairs':2560,'full_operation_control_pairs':128,
        'operation_count':len(OPERATIONS),'main_operations_per_pair':8,
        'expected_unique_prompts':len(tasks),'hard_capture_budget':args.max_prompts,
        'operation_holdouts':list(HELD_OUT_OPERATIONS),
        'operation_counts':dict(op_counts),'family_counts':dict(fam_counts),
        'split_counts':dict(Counter(r['split'] for r in pairs)),
        'prompt_split_counts':dict(Counter(r['split'] for r in tasks)),
        'capture':{'model':args.model,'store_dtype':args.store_dtype,
                   'max_prompt_tokens':args.max_prompt_tokens,
                   'stored_layers': 'all' if args.store_layers=='all' else [int(x) for x in args.store_layers.split(',')],
                   'full_token_states_unpooled':True},
        'pairs_sha256':digest(pair_text),'tasks_sha256':digest(task_text),
        'task_file':'prompts.jsonl','pair_file':'pairs.jsonl',
        'operation_file':'operations.json',
        'analysis_defaults_file':'analysis_defaults.json',
        'note':'No coefficients, selected activation coordinates, or LASSO models inherited. '
               'Main train operations exclude four complete variants. Full-operation control pairs are held out.'
    }
    root=args.output_root.expanduser().resolve()
    if write:
        root.mkdir(parents=True,exist_ok=True)
        analysis_defaults={
            'description':'Suggested v3 analysis configuration only; NOT applied during capture',
            'representation':'paired','alpha_ratio':0.002,'stage':5,'layers':[16,28],
            'readout_scope':'suffix','tail_tokens':4,
            'solver_max_iter':10000,'solver_retry_multiplier':3,'kkt_iters':12,
            'fit_splits':['train'],'model_selection_split':'val',
            'final_evaluation_splits':['test','control'],
            'operation_holdouts':list(HELD_OUT_OPERATIONS),
            'note':'Refit all LASSO heads and activation coordinates from scratch. Do not fit on control or test.',
        }
        outputs={root/'manifest.json':json.dumps(manifest,indent=2,sort_keys=True)+'\n',
                 root/'analysis_defaults.json':json.dumps(analysis_defaults,indent=2,sort_keys=True)+'\n',
                 root/'prompts.jsonl':task_text,root/'pairs.jsonl':pair_text,
                 root/'operations.json':json.dumps(operations_doc,indent=2)+'\n'}
        for path,content in outputs.items():
            if path.exists() and path.read_text(encoding='utf-8')!=content:
                raise RuntimeError(f'REGISTER_V3_MANIFEST_DRIFT:{path}')
        if any(root.iterdir()) and not (root/'manifest.json').exists():
            raise RuntimeError('REGISTER_V3_ROOT_NOT_EMPTY_OR_INCOMPLETE')
        for path,content in outputs.items():
            if not path.exists(): write_atomic(path,content)
    emit('register_v3_plan',pairs=len(pairs),main_pairs=2560,control_pairs=128,
         operations=len(OPERATIONS),unique_prompts=len(tasks),
         main_prompts=20480,control_prompts=3072,
         split_counts=manifest['split_counts'],
         held_out_operations=list(HELD_OUT_OPERATIONS),
         capture_layers=manifest['capture']['stored_layers'],
         root=str(root),example=tasks[0]['prompt'],written=write)
    return manifest,tasks


def check_capture(args,tasks):
    root=args.output_root.expanduser().resolve()
    total=len(tasks)
    captured=sum((root/'records'/(task['sha256']+'.npz')).is_file() for task in tasks)
    emit('register_v3_capture_status',captured=captured,required=total,missing=total-captured,
         complete=(captured==total),root=str(root))
    return captured==total


def capture(args,tasks):
    # Load the frozen Qwen cassette only for actual capture, not generation.
    import numpy as np
    from threadpoolctl import threadpool_limits
    import nanojev_full_activation_sparse_hierarchy as sparse
    model_root=args.output_root.expanduser().resolve()
    identity_path=model_root/'model_identity.json'
    identity_existing=json.loads(identity_path.read_text(encoding='utf-8')) if identity_path.is_file() else None
    stored_layers=None if args.store_layers=='all' else [int(v) for v in args.store_layers.split(',')]
    pending=[task for task in tasks if not (model_root/'records'/(task['sha256']+'.npz')).is_file()]
    if not pending:
        if identity_existing is None:
            raise RuntimeError('REGISTER_V3_IDENTITY_MISSING')
        emit('register_v3_capture_complete',captured=len(tasks),remaining=0,root=str(model_root))
        return
    if len(pending)!=len(tasks) and not args.resume:
        raise RuntimeError('REGISTER_V3_PARTIAL_CAPTURE_REQUIRES_--resume')
    with threadpool_limits(limits=args.threads):
        torch,model,tokenizer,device,info=sparse.cassette.load_source(args.model,args)
        identity={'model':args.model,'model_info':info}
        if identity_existing is not None and identity_existing!=identity:
            raise RuntimeError('REGISTER_V3_MODEL_IDENTITY_DRIFT')
        if identity_existing is None:
            write_atomic(identity_path,json.dumps(identity,indent=2,sort_keys=True)+'\n')
        (model_root/'records').mkdir(parents=True,exist_ok=True)
        completed=0;new=0;start=time.monotonic()
        for task in tasks:
            path=model_root/'records'/(task['sha256']+'.npz')
            if path.is_file():
                # Resume must reject mismatched/corrupt records rather than
                # silently treating a file's presence as valid evidence.
                with np.load(path,allow_pickle=False) as f:
                    md=json.loads(str(f['metadata'].item()))
                    tensor=f['hidden_states']
                if (md.get('sha256')!=task['sha256'] or md.get('prompt')!=task['prompt']
                    or md.get('identity')!=identity or md.get('shape')!=list(tensor.shape)
                    or md.get('stored_layers')!=('all' if stored_layers is None else stored_layers)
                    or tensor.ndim!=3 or not np.isfinite(tensor).all()):
                    raise RuntimeError(f'REGISTER_V3_EXISTING_RECORD_INVALID:{path}')
            elif args.max_new_captures is None or new<args.max_new_captures:
                md,states=sparse.capture_full(torch,model,tokenizer,task['prompt'],device,
                                              args.max_prompt_tokens,args.store_dtype)
                all_levels=states.shape[0]
                if stored_layers is not None:
                    if any(x<0 or x>=all_levels for x in stored_layers):
                        raise RuntimeError(f'REGISTER_V3_CAPTURE_LAYER_OOB:{stored_layers}:{all_levels}')
                    states=states[stored_layers,:,:]
                md.update({'identity':identity,'stored_layers':('all' if stored_layers is None else stored_layers),
                           'original_hidden_state_levels':all_levels,'shape':list(states.shape),
                           'generator_schema':SCHEMA})
                tmp=path.with_name(path.name+'.partial')
                with tmp.open('wb') as stream:
                    np.savez_compressed(stream,metadata=json.dumps(md,sort_keys=True),hidden_states=states)
                tmp.replace(path)
                new+=1
            else:
                break
            completed+=1
            if completed%args.report_every==0 or completed==len(tasks):
                emit('register_v3_capture_progress',processed=completed,
                     captured_before_run=len(tasks)-len(pending),new_captures=new,
                     total=len(tasks),seconds=round(time.monotonic()-start,2))
        del model
    check_capture(args,tasks)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--prepare-only',action='store_true',help='Count and preview only; write nothing.')
    modes.add_argument('--generate-only',action='store_true',help='Create deterministic JSONL + manifest, no model load (default).')
    modes.add_argument('--capture-only',action='store_true',help='Generate/verify plan, then capture Qwen states.')
    modes.add_argument('--check-capture',action='store_true',help='Check progress without loading Qwen.')
    modes.add_argument('--self-test',action='store_true')
    p.add_argument('--output-root',type=Path,default=DEFAULT_ROOT)
    p.add_argument('--seed',type=int,default=20261013)
    p.add_argument('--max-prompts',type=int,default=24000)
    p.add_argument('--model',choices=('qwen','pythia','tinystories'),default='qwen')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='float16')
    p.add_argument('--store-dtype',choices=('float16','float32'),default='float16')
    p.add_argument('--store-layers',default='16,28',help='Selected hidden state levels to save, or all.')
    p.add_argument('--max-prompt-tokens',type=int,default=256)
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--threads',type=int,default=2)
    p.add_argument('--report-every',type=int,default=100)
    p.add_argument('--max-new-captures',type=int,default=None,
                   help='For smoke/resume: stop after N new captures, leaving other tasks pending.')
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.report_every<1 or args.threads<1 or args.max_prompt_tokens<1:
        raise ValueError('REGISTER_V3_INVALID_ARGUMENTS')
    if args.max_new_captures is not None and args.max_new_captures<1:
        raise ValueError('REGISTER_V3_INVALID_CAPTURE_LIMIT')
    if args.store_layers!='all':
        try: layers=[int(v.strip()) for v in args.store_layers.split(',')]
        except ValueError as e: raise ValueError('REGISTER_V3_INVALID_LAYERS') from e
        if not layers or min(layers)<0 or len(layers)!=len(set(layers)):
            raise ValueError('REGISTER_V3_INVALID_LAYERS')
    if args.self_test:
        pairs,tasks,ops,families=build_plan(args.seed,args.max_prompts)
        assert len(pairs)==2688 and len(tasks)==23552
        assert len(set(t['sha256'] for t in tasks))==23552
        assert len({t['operation'] for t in tasks if t['cohort']=='control'})==24
        assert not any(t['operation'] in HELD_OUT_OPERATIONS for t in tasks if t['cohort']=='main')
        assert render(2000,-3,OPERATIONS[0]).startswith('XXXXXX = 2000, ')
        emit('register_v3_self_test_passed',prompts=len(tasks))
        return
    manifest,tasks=setup(args,write=not args.prepare_only)
    if args.prepare_only or args.generate_only or not(args.capture_only or args.check_capture): return
    if args.check_capture: check_capture(args,tasks)
    else: capture(args,tasks)


if __name__=='__main__':
    try: main()
    except (ValueError,RuntimeError,FileNotFoundError,AssertionError) as exc:
        emit('register_v3_error',message=str(exc))
        sys.exit(1)
