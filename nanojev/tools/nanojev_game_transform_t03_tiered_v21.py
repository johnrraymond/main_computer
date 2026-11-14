#!/usr/bin/env python3
"""T03 V2.1: native T02 bridge, staged hidden-Y arithmetic, and gated retention.

One-command entry points, run from the main_computer repository:
    python -m tools.nanojev_game_transform_t03_tiered_v21 train --steps 220002
    python -m tools.nanojev_game_transform_t03_tiered_v21 probe

All new training phases are isolated under game_transform_t03_tiered_v21/stage-NN.
Previous T02 and T03 V1 checkpoints and sealed source generators are untouched.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager, redirect_stdout
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import random
import sqlite3
from types import SimpleNamespace
import uuid

from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_phase_cutover as cutover
from tools import nanojev_game_transform_t03_tiered as v1
from tools import nanojev_game_transform_t03_live_probe as live_v1
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_transform_level_probe as probe

SCHEMA = 'nanojev.t03-computation-bridge.v2.1'
BRIDGE = ('T03a0', 'T03a1', 'T03a2', 'T03b0', 'T03b1', 'T03b2')
LATE = ('T03c','T03d','T03e','T03f','T03g','T03h','T03i')
TIERS = BRIDGE + LATE
EVAL_TIERS = TIERS + ('T03j',)
# Stage 0 contains only one hidden-Y slot; original T00/T02f get full supervision.
STAGES = (
    {'retention':4,'T00':9,'T02f':9,'T03a0':12,'T03a1':5,'T03a2':3,'T03b0':1},
    {'retention':4,'T00':7,'T02f':7,'T03a0':5,'T03a1':6,'T03a2':5,'T03b0':10,'T03b1':4},
    {'retention':4,'T00':6,'T02f':6,'T03a0':3,'T03a1':4,'T03a2':4,'T03b0':8,'T03b1':12,'T03b2':5},
    {'retention':4,'T00':6,'T02f':6,'T03a0':2,'T03a1':3,'T03a2':3,'T03b0':5,'T03b1':10,'T03b2':9,'T03c':3,'T03d':2},
    {'retention':4,'T00':6,'T02f':6,'T03a0':2,'T03a1':2,'T03a2':2,'T03b0':3,'T03b1':5,'T03b2':6,'T03c':6,'T03d':5,'T03e':3,'T03f':3,'T03g':2,'T03h':1},
    {'retention':4,'T00':6,'T02f':6,'T03a0':2,'T03a1':2,'T03a2':2,'T03b0':2,'T03b1':3,'T03b2':4,'T03c':5,'T03d':5,'T03e':4,'T03f':4,'T03g':4,'T03h':4,'T03i':4},
)
# Requirements are evaluated on DEV only. T00/T02f additionally require
# no more than five percentage points of parent degradation at 10% tolerance.
GATES = (
    {'T03a0':('0.05','paired_xy',.90), 'T03a1':('0.1','paired_xy',.75)},
    {'T03a2':('0.1','paired_xy',.75), 'T03b0':('integer_exact','y',.40)},
    {'T03b1':('integer_exact','y',.40)},
    {'T03b2':('integer_exact','y',.35), 'T03c':('0.1','y',.15)},
    {'T03d':('0.1','y',.25), 'T03f':('0.1','y',.20)},
    {},
)
POOL_CAP = {'T03a0':128,'T03a1':128,'T03a2':128,'T03b0':48,'T03b1':256,'T03b2':512}
assert all('T08' not in q and 'T03j' not in q for q in STAGES)


def _phase(stage: int) -> str:
    return f't03_v21_s{stage}'


def _stage_dir(root:Path,stage:int)->Path:
    return root / f'stage-{stage:02d}'


def _latest_stage(root:Path)->int:
    found=[i for i in range(len(STAGES)) if (_stage_dir(root,i)/'latest.pt').is_file()]
    return max(found) if found else 0


def _choose(seed:int,level:str,split:str,index:int)->random.Random:
    digest=hashlib.sha256(f'{SCHEMA}|{seed}|{level}|{split}|{index}'.encode()).digest()
    return random.Random(int.from_bytes(digest[:8],'big'))


def bridge_case(level:str,seed:int,split:str,index:int)->dict:
    """Disjoint X domains and oracle-computed Y; no train/dev/test duplicates."""
    if level not in BRIDGE or split not in ('train','dev','test') or index<0:
        raise ValueError('BRIDGE_CASE_INVALID')
    rng=_choose(seed,level,split,index)
    if level=='T03b0':
        low,high=-9,9; deltas=tuple(range(1,9))
    elif level=='T03b1':
        low,high=-30,30; deltas=tuple(i for i in range(-15,16) if i)
    elif level=='T03b2':
        low,high=-400,400; deltas=tuple(i for i in range(-100,101) if i)
    else:
        low,high=-16,16; deltas=tuple(i for i in range(-12,13) if i)
    group={'train':0,'dev':1,'test':2}[split]
    xs=tuple(x for x in range(low,high+1)
             if x%3==group and (level in BRIDGE[:3] or x!=0))
    x=rng.choice(xs); delta=rng.choice(deltas);y=x+delta
    data={'kind':'add','delta':delta,'tier':level,'visible_y':level in BRIDGE[:3]}
    return {'id':hashlib.sha256(ex.canon([SCHEMA,seed,split,level,x,delta]).encode()).hexdigest(),
            'x':x,'y':y,'data':data,'operator':'positive_affine',
            'game':'synthetic_'+level,'tier':level,'split':split}


def bridge_pool(level:str,seed:int,count:int)->list[dict]:
    count=min(count,POOL_CAP[level]);seen=set();items=[];attempts=0
    while len(items)<count:
        row=bridge_case(level,seed,'train',attempts);attempts+=1
        signature=(row['x'],row['data']['delta'])
        if signature not in seen:
            seen.add(signature);items.append(row)
        if attempts>100*count:raise RuntimeError('BRIDGE_POOL_EXHAUSTED')
    return items


def bridge_prompt(level:str,row:dict,orientation:str,mix,base)->str:
    if level not in BRIDGE or orientation not in ('forward','reordered'):
        raise ValueError('INVALID_BRIDGE_PRESENTATION')
    native=probe.native_prompt(mix,base,row,orientation=='reordered')
    if level=='T03a0':
        return native  # Exact bytes of successful T00/T02 register presentation.
    delta=row['data']['delta']
    if level=='T03a2':
        rule=f'Rule: start with XXXXXX and add {delta} to get YYYYYY.'
    else:
        rule=(f'Expression (not a register assignment): '
              f'XXXXXX + OFFSET -> YYYYYY; OFFSET is {delta:+d}.')
    if level in BRIDGE[3:]:
        lines=native.splitlines(keepends=True)
        matched=[i for i,line in enumerate(lines) if line.startswith('YYYYYY = ')]
        if len(matched)!=1:raise AssertionError('NATIVE_Y_REGISTER_NOT_FOUND')
        i=matched[0]
        lines[i]='YYYYYY = [compute from expression]\n'
        native=''.join(lines)
        if f'YYYYYY = {row["y"]}' in native:
            raise AssertionError('HIDDEN_Y_VALUE_LEAK')
    # Only the native block may contain a register assignment. OFFSET is a
    # separate input, not the Y target. The rule deliberately avoids 'YYYYYY ='.
    # Keep the original native register block byte-for-byte where visible;
    # add exactly one equation or explanation before it.
    return rule+'\n'+native


@contextmanager
def install_adapter(seed:int,pool_size:int):
    """Reuse trainer without modifying its hash-checked source or V1 generator."""
    original=(ladder.LEVELS,ladder.TRAINABLE,ladder.contextual_prompt,ladder.eligible_pools)
    def prompt(level,row,orientation,mix,base):
        if level in BRIDGE:return bridge_prompt(level,row,orientation,mix,base)
        if level in LATE:
            if level=='T03i':return original[2]('T03',row,orientation,mix,base)
            return v1.tier_prompt(level,row,orientation)
        return original[2](level,row,orientation,mix,base)
    def pools(db,model,quota):
        old={k:v for k,v in quota.items() if k in original[1]}
        if 'T03i' in quota:old['T03']=quota['T03i']
        result=original[3](db,model,old)
        if 'T03i' in quota:result['T03i']=result.pop('T03')
        for level in TIERS:
            if level not in quota or level=='T03i':continue
            source=bridge_pool(level,seed,pool_size) if level in BRIDGE else v1.generated_pool(level,seed,pool_size)
            valid=[]
            for row in source:
                views=[prompt(level,row,ori,model['mix'],model['base']) for ori in ('forward','reordered')]
                if all(probe.token_check(model['tokenizer'],txt,model['config']['max_prompt_tokens'])['status']=='ok' for txt in views):
                    valid.append(row)
            if not valid:raise RuntimeError('T03_V21_NO_VALID_EXAMPLES:'+level)
            result[level]=valid
        return result
    try:
        ladder.LEVELS=original[0]+TIERS
        ladder.TRAINABLE=original[1]+TIERS
        ladder.contextual_prompt=prompt
        ladder.eligible_pools=pools
        yield
    finally:
        ladder.LEVELS,ladder.TRAINABLE,ladder.contextual_prompt,ladder.eligible_pools=original


def prepare_stage(stage:int,args,steps:int)->tuple[dict,bool]:
    if stage<0 or stage>=len(STAGES):raise ValueError('UNKNOWN_CURRICULUM_STAGE')
    output=_stage_dir(args.output.resolve(),stage)
    parent=args.from_output.resolve() if stage==0 else _stage_dir(args.output.resolve(),stage-1)
    slots=STAGES[stage]
    spec_args=SimpleNamespace(from_output=parent,output=output,
        database=args.database.resolve(),source_checkpoint=args.source_checkpoint.resolve(),
        diagnostic=args.diagnostic.resolve(),slots='stage-sealed',steps=steps)
    old_phase,old_parse,old_expected=cutover.PHASE,cutover.parse_slots,cutover._expected_spec
    def parse(text):
        if text!='stage-sealed':raise ValueError('V2_STAGE_QUOTA_OVERRIDE_FORBIDDEN')
        return dict(slots)
    def expected(parent_config,quotas):
        spec=old_expected(parent_config,quotas)
        spec.update({'t03_v21_generator_sha256':ex.filehash(Path(__file__)),
                     't03_v21_stage':stage,'t03_v21_pool_size':args.pool_per_tier,
                     't03_v21_schema':SCHEMA})
        return spec
    try:
        cutover.PHASE=_phase(stage)
        cutover.parse_slots=parse
        cutover._expected_spec=expected
        with install_adapter(args.seed,args.pool_per_tier):
            config,created=cutover.prepare(spec_args)
    finally:
        cutover.PHASE,cutover.parse_slots,cutover._expected_spec=old_phase,old_parse,old_expected
    saved=json.loads((output/'level-map.json').read_text())
    if (saved.get('t03_v21_generator_sha256')!=ex.filehash(Path(__file__)) or
        saved.get('t03_v21_stage')!=stage or saved.get('t03_v21_pool_size')!=args.pool_per_tier):
        raise RuntimeError('T03_V21_SOURCE_OR_POOL_DRIFT_START_NEW_LINEAGE')
    config_file=output/'ladder-config.json'
    if created:
        config['cache_per_level']=args.cache_per_level
        config['refresh_every']=args.refresh_every
        ex.write_json(config_file,config)
    elif config['cache_per_level']!=args.cache_per_level or config['refresh_every']!=args.refresh_every:
        raise RuntimeError('T03_V21_CACHE_DRIFT')
    if args.seed!=config['seed'] or config['op_weight']!=0:
        raise RuntimeError('T03_V21_PARENT_SEED_OR_OPERATOR_CONTRACT')
    return config,created


def train_chunk(args,stage:int,target:int)->dict:
    config,created=prepare_stage(stage,args,target)
    argv=['train','--database',str(args.database),'--source-checkpoint',str(args.source_checkpoint),
          '--diagnostic',str(args.diagnostic),'--level-map',str(_stage_dir(args.output,stage)/'level-map.json'),
          '--phase',_phase(stage),'--output',str(_stage_dir(args.output,stage)),
          '--steps',str(target),'--save-every',str(args.save_every),
          '--report-every',str(args.report_every),'--max-wall-hours',str(args.max_wall_hours),
          '--seed',str(config['seed']),'--lr',str(config['lr']),
          '--op-weight','0','--cache-per-level',str(args.cache_per_level),
          '--retention-cache',str(config['retention_cache']),
          '--refresh-every',str(args.refresh_every),'--device',args.device,'--dtype',args.dtype]
    if args.allow_model_download:argv.append('--allow-model-download')
    with install_adapter(args.seed,args.pool_per_tier):
        print(ex.canon({'event':'t03_v21_stage_train','stage':stage,'created':created,
                        'phase':_phase(stage),'target':target,'quotas':STAGES[stage]}),flush=True)
        return ladder.train(ladder.parser().parse_args(argv))


def integer_exact_score(records:list[dict])->dict:
    """Both orientations must round to the correct integer in each register.

    Zero is evaluated in absolute units, not as a relative percentage. This
    diagnostic is kept separate from the historical 5%/10% paired scores.
    """
    pairs={}
    for r in records:pairs.setdefault(r['case_id'],{})[r['orientation']]=r
    count=nx=ny=nboth=0
    for orientations in pairs.values():
        a,b=orientations.get('forward'),orientations.get('reordered')
        if not a or not b or any(r['status']!='ok' or 'prediction' not in r for r in (a,b)):
            continue
        truth=a['truth']
        x=all(abs(float(r['prediction'][0])-truth['x'])<=.5 for r in (a,b))
        y=all(abs(float(r['prediction'][1])-truth['y'])<=.5 for r in (a,b))
        count+=1;nx+=x;ny+=y;nboth+=x and y
    return {'n':count,'x':nx/count if count else 0.,
            'y':ny/count if count else 0.,'paired_xy':nboth/count if count else 0.}


def offset_echo_score(records:list[dict],items:list[dict])->dict:
    """Paired diagnostic: Y follows OFFSET versus correctly calculated X+OFFSET."""
    grouped={}
    for rec in records:
        if rec['status']=='ok' and 'prediction' in rec:
            grouped.setdefault(rec['case_id'],{})[rec['orientation']]=rec
    by_id={item['id']:item for item in items}
    n=echo=near=0
    for key,views in grouped.items():
        if 'forward' not in views or 'reordered' not in views:
            continue
        case=by_id[key]
        delta=case['data']['delta']
        preds=[float(views[ori]['prediction'][1]) for ori in ('forward','reordered')]
        n+=1
        echo+=int(all(abs(p-delta)<=.5 for p in preds))
        near+=int(all(abs(p-case['y'])<=.5 for p in preds))
    return {'n':n,'echo_rate':echo/n if n else 0.,
            'y_exact_rate':near/n if n else 0.}


def evaluate(args,checkpoint:Path,limit:int,save_report:Path|None=None,
             levels:set[str]|None=None, splits:tuple[str,...]|None=None)->dict:
    """Identical heldouts for baseline/trained, plus independent T00/T02f retention."""
    import torch
    db=args.database.resolve();src=args.source_checkpoint.resolve()
    parent=args.from_output.resolve()/'latest.pt'
    paths=(db,src,checkpoint.resolve(),parent)
    if not all(p.is_file() for p in paths):raise FileNotFoundError('T03_V21_PROBE_INPUT_MISSING')
    hashes=tuple(ex.filehash(p) for p in paths)
    rows={}
    selected=set(('T00','T02f')+EVAL_TIERS) if levels is None else set(levels)
    if selected - set(('T00','T02f')+EVAL_TIERS):
        raise ValueError('UNRECOGNIZED_V2_PROBE_TIER')
    partitions=('dev','test') if splits is None else tuple(splits)
    if not partitions or any(x not in ('dev','test') for x in partitions):
        raise ValueError('INVALID_V2_PROBE_SPLIT')
    for split in partitions:
        for tier in EVAL_TIERS:
            if tier not in selected or tier=='T03i':continue
            rows[(split,tier)]=([bridge_case(tier,args.seed,split,i) for i in range(limit)]
                                 if tier in BRIDGE else
                                 [v1.oracle_case(tier,args.seed,split,i) for i in range(limit)])
    if selected & {'T00','T02f','T03i'}:
        with closing(sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)) as cn:
            native_splits=partitions+(('game_transfer',) if splits is None and 'T03i' in selected else ())
            for split in native_splits:
                items=ladder.materialize_source(cn,probe.select_balanced(ex.rows_for(cn,split),limit))
                good=[r for r in items if probe.available_integer(r)]
                for tier in ('T00','T02f'):
                    if tier in selected and split!='game_transfer':rows[(split,tier)]=good
                if 'T03i' in selected:
                    rows[(split,'T03i')]=[r for r in good if r['x']!=r['y']]
    report={'schema':SCHEMA,'generator_sha256':ex.filehash(Path(__file__)),
            'database_sha256':hashes[0],'source_checkpoint_sha256':hashes[1],
            'checkpoint_sha256':hashes[2],'baseline_checkpoint_sha256':hashes[3],
            'scores':{},'examples':{},'steps':{}}
    for label,cp_path in [('baseline',parent),('trained',checkpoint)]:
        model=ex.load_current(src,args.device,args.dtype,args.allow_model_download,args.seed)
        cp=torch.load(cp_path,map_location='cpu',weights_only=True)
        if cp.get('source_sha256')!=hashes[1] or cp.get('database_sha256')!=hashes[0]:
            raise RuntimeError('T03_V21_PROBE_CHECKPOINT_IDENTITY_DRIFT')
        model['decoder'].load_state_dict(cp['decoder'],strict=True)
        model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
        model['decoder'].eval();model['gamehead'].eval()
        report['steps'][label]=int(cp['step'])
        for (split,tier),items in rows.items():
            records=[]
            for item in items:
                for orientation in ('forward','reordered'):
                    if tier in BRIDGE:prompt=bridge_prompt(tier,item,orientation,model['mix'],model['base'])
                    elif tier=='T03i':prompt=probe.render('T03',item,orientation,model['mix'],model['base'])
                    elif tier in ('T00','T02f'):
                        prompt=ladder.contextual_prompt(tier,item,orientation,model['mix'],model['base'])
                    else:prompt=v1.tier_prompt(tier,item,orientation)
                    validity=probe.token_check(model['tokenizer'],prompt,model['config']['max_prompt_tokens'])
                    rec={'case_id':item['id'],'game':item['game'],'operator':item['operator'],
                         'split':split,'level':tier,'orientation':orientation,
                         'truth':{'x':item['x'],'y':item['y']},'status':validity['status']}
                    if validity['status']=='ok':
                        rec['prediction'],rec['predicted_operator']=ex.predict_one(model,prompt)
                    records.append(rec)
            scoremap={}
            for th in (.05,.10):
                result=probe.score(records,tier,th)
                scoremap[str(th)]={'n':result['n'],'paired_xy':result['paired']['joint'],
                                  'x':result['paired']['x'],'y':result['paired']['y'],
                                  'unscorable':result['unscorable']}
            scoremap['integer_exact']=integer_exact_score(records)
            if tier in BRIDGE:
                # For each paired case, distinguish genuine computation from
                # simply returning the offset named in the expression.
                scoremap['offset_diagnostic']=offset_echo_score(records,items)
            key=f'{label}/{split}/{tier}'
            report['scores'][key]=scoremap
            if label=='trained' and split=='dev':
                # Real examples are for diagnosis, not stage promotion.
                report['examples'][tier]=[
                    {'x':r['truth']['x'],'y':r['truth']['y'],
                     'predicted_x':round(float(r['prediction'][0]),4),
                     'predicted_y':round(float(r['prediction'][1]),4),
                     **({'offset':r['truth']['y']-r['truth']['x']} if tier in BRIDGE else {})}
                    for r in records if r['orientation']=='forward' and 'prediction' in r
                ][:3]
        del model
        if args.device=='cuda':torch.cuda.empty_cache()
    if any(ex.filehash(p)!=h for p,h in zip(paths,hashes)):
        raise RuntimeError('T03_V21_PROBE_INPUT_CHANGED_DURING_READ')
    if save_report:ex.write_json(save_report,report)
    return report


def stage_gate(report:dict,stage:int)->tuple[bool,list[str]]:
    failures=[];scores=report['scores']
    for tier in ('T00','T02f'):
        key='dev/'+tier
        old=scores.get('baseline/'+key,{}).get('0.1')
        new=scores.get('trained/'+key,{}).get('0.1')
        if not old or not new or not new['n'] or new['n']!=old['n']:
            failures.append(tier+':retention_evidence_missing');continue
        floor=max(.75,old['paired_xy']-.05)
        if new['paired_xy']+1e-8<floor:
            failures.append(f'{tier}:retention={new["paired_xy"]:.3f} < {floor:.3f}')
    for tier,(tol,metric,minimum) in GATES[stage].items():
        data=scores.get(f'trained/dev/{tier}',{}).get(tol)
        if not data or not data['n'] or data[metric]+1e-8<minimum:
            observed='missing' if not data else f'{data[metric]:.3f}'
            failures.append(f'{tier}:{metric}={observed} < {minimum:.3f}')
    return not failures,failures


def _progress_path(root:Path)->Path:
    return root/'curriculum-progress.json'


def _progress(root:Path)->dict:
    p=_progress_path(root)
    if not p.is_file():return {'schema':SCHEMA,'stage':_latest_stage(root),'consecutive_passes':0}
    doc=json.loads(p.read_text())
    if doc.get('schema')!=SCHEMA:raise RuntimeError('T03_V21_PROGRESS_SCHEMA_DRIFT')
    return doc


def _save_progress(root:Path,doc:dict)->None:
    ex.write_json(_progress_path(root),doc)


def train(args)->dict:
    import torch
    if args.chunk_steps<1 or args.gate_limit<1 or args.steps<1:
        raise ValueError('INVALID_V2_TRAINING_LIMIT')
    root=args.output.resolve()
    protected={args.from_output.resolve(),
               (Path.home()/'NanoJev/runs/game_transform_t03_tiered').resolve(),
               (Path.home()/'NanoJev/runs/game_transform_t03_tiered_v2').resolve()}
    if root in protected:
        raise ValueError('T03_V21_MUST_NOT_OVERWRITE_AN_EXISTING_LINEAGE')
    root.mkdir(parents=True,exist_ok=True)
    state=_progress(root)
    stage=max(int(state['stage']),_latest_stage(root))
    consecutive=int(state['consecutive_passes']) if stage==int(state['stage']) else 0
    while True:
        path=_stage_dir(root,stage)
        if path.joinpath('latest.pt').is_file():
            cp=torch.load(path/'latest.pt',map_location='cpu',weights_only=True)
        else:
            parent=args.from_output if stage==0 else _stage_dir(root,stage-1)
            cp=torch.load(parent/'latest.pt',map_location='cpu',weights_only=True)
        step=int(cp['step'])
        if step>=args.steps:
            return {'step':step,'stage':stage,'stop_reason':'target_reached','run':str(root)}
        goal=min(args.steps,step+args.chunk_steps)
        outcome=train_chunk(args,stage,goal)
        report_dir=path/'live-probes';report_dir.mkdir(parents=True,exist_ok=True)
        report_path=report_dir/f'gate-step-{outcome["step"]:09d}.json'
        with redirect_stdout(StringIO()):
            rep=evaluate(args,path/'latest.pt',args.gate_limit,report_path,
                levels={'T00','T02f'}|set(GATES[stage]),splits=('dev',))
        passed,failures=stage_gate(rep,stage)
        consecutive=consecutive+1 if passed else 0
        state={'schema':SCHEMA,'stage':stage,'consecutive_passes':consecutive,
               'last_evaluated_step':outcome['step'],'last_report':str(report_path),
               'passed':passed,'failures':failures}
        _save_progress(root,state)
        print(ex.canon({'event':'t03_v21_gate','stage':stage,'step':outcome['step'],
                        'passed':passed,'consecutive_passes':consecutive,'failures':failures,
                        'report':str(report_path)}),flush=True)
        if consecutive>=2 and stage<len(STAGES)-1:
            stage+=1
            prepare_stage(stage,args,outcome['step']+1)
            consecutive=0
            state.update({'stage':stage,'consecutive_passes':0,'promoted_from_step':outcome['step']})
            _save_progress(root,state)
            print(ex.canon({'event':'t03_v21_promoted','next_stage':stage,
                            'source_step':outcome['step']}),flush=True)
        if outcome['step']>=args.steps or outcome['stop_reason']!='target_reached':
            return {'step':outcome['step'],'stage':stage,'stop_reason':outcome['stop_reason'],
                    'gate':state,'run':str(root)}


def _aggregate(report,split:str,tier:str,tol:str,label:str,metric:str)->tuple[int,float]:
    key=f'{label}/{split}/{tier}'
    cell=report['scores'].get(key,{}).get(tol)
    return (cell['n'],cell[metric]) if cell else (0,0.)


def summary(report:dict,stage:int)->str:
    lines=[f'T03 V2.1 LIVE PROBE | parent {report["steps"]["baseline"]:,} -> latest {report["steps"]["trained"]:,} | stage {stage}',
           'Paired accuracy within 10%; retained skills and computation (dev+test)',
           f'{"Tier":<15} {"N":>4} {"Parent":>8} {"Now":>8} {"Y 5%":>8} {"Y 10%":>8} {"Y int":>8}']
    for tier in ('T00','T02f')+EVAL_TIERS:
        n=0;sums={'baseline':0.,'trained':0.,'y5':0.,'y10':0.,'yint':0.}
        for split in ('dev','test'):
            bn,b=_aggregate(report,split,tier,'0.1','baseline','paired_xy')
            tn,t=_aggregate(report,split,tier,'0.1','trained','paired_xy')
            _,y5=_aggregate(report,split,tier,'0.05','trained','y')
            _,y10=_aggregate(report,split,tier,'0.1','trained','y')
            _,yint=_aggregate(report,split,tier,'integer_exact','trained','y')
            if bn!=tn:raise RuntimeError('V2_PROBE_CASE_COUNT_MISMATCH')
            n+=tn;sums['baseline']+=bn*b;sums['trained']+=tn*t
            sums['y5']+=tn*y5;sums['y10']+=tn*y10;sums['yint']+=tn*yint
        if not n:continue
        lines.append(f'{tier:<15} {n:>4} {100*sums["baseline"]/n:>7.1f}% '
                     f'{100*sums["trained"]/n:>7.1f}% {100*sums["y5"]/n:>7.1f}% '
                     f'{100*sums["y10"]/n:>7.1f}% {100*sums["yint"]/n:>7.1f}%')
    old=report['scores'].get('baseline/game_transfer/T03i',{}).get('0.1')
    new=report['scores'].get('trained/game_transfer/T03i',{}).get('0.1')
    if old and new:
        lines.append(f'T03i transfer     {new["n"]:>4} {100*old["paired_xy"]:>7.1f}% '
                     f'{100*new["paired_xy"]:>7.1f}%   {100*new["y"]:>5.1f}% Y')
    ok,errors=stage_gate(report,stage)
    lines.append('Promotion gate: '+('PASS' if ok else 'BLOCKED — '+'; '.join(errors)))
    echo=report['scores'].get('trained/dev/T03b0',{}).get('offset_diagnostic')
    if echo:
        lines.append(f'T03b0 diagnostic (dev): {echo["echo_rate"]:.1%} of paired Y predictions echo OFFSET; '
                     f'{echo["y_exact_rate"]:.1%} calculate Y within ±0.5')
    if 'T03b0' in report.get('examples',{}):
        for exm in report['examples']['T03b0'][:3]:
            lines.append(f'Example T03b0: X={exm["x"]}, offset={exm["offset"]:+d}, '
                         f'expected Y={exm["y"]}, predicted Y={exm["predicted_y"]}')
    return '\n'.join(lines)


def run_probe(args)->tuple[dict,Path]:
    root=args.output.resolve();stage=_latest_stage(root);run=_stage_dir(root,stage)
    src=args.experimental_checkpoint.resolve() if args.experimental_checkpoint else run/'latest.pt'
    folder=root/'live-probes';folder.mkdir(parents=True,exist_ok=True)
    snapshot,step,digest=live_v1.snapshot_checkpoint(src,folder)
    dest=folder/f't03-v21-step-{step:09d}-{datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}.json'
    try:
        print(f'Probing latest saved step {step:,} from {src}',flush=True)
        report=evaluate(args,snapshot,args.limit,dest)
        if report['steps']['trained']!=step or report['checkpoint_sha256']!=digest:
            raise RuntimeError('T03_V21_LIVE_SNAPSHOT_IDENTITY_MISMATCH')
        print(summary(report,stage),flush=True)
        print(f'Report: {dest}',flush=True)
        return report,dest
    finally:
        snapshot.unlink(missing_ok=True)


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',choices=('train','probe'),default='probe')
    p.add_argument('--output',type=Path,default=Path.home()/'NanoJev/runs/game_transform_t03_tiered_v21')
    p.add_argument('--from-output',type=Path,default=Path.home()/'NanoJev/runs/game_transform_ladder_t02_corrected')
    p.add_argument('--database',type=Path,default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
    p.add_argument('--source-checkpoint',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--diagnostic',type=Path,default=Path('runtime/game-transform-experiment/t02-isolation-v2.json'))
    p.add_argument('--experimental-checkpoint',type=Path,default=None)
    p.add_argument('--steps',type=int,default=230000,help='Cumulative total, parent is step 220000')
    p.add_argument('--chunk-steps',type=int,default=1000,help='Gate evaluations every N additional optimizer steps')
    p.add_argument('--gate-limit',type=int,default=48)
    p.add_argument('--save-every',type=int,default=500)
    p.add_argument('--report-every',type=int,default=100)
    p.add_argument('--max-wall-hours',type=float,default=8.)
    p.add_argument('--cache-per-level',type=int,default=64)
    p.add_argument('--refresh-every',type=int,default=1000)
    p.add_argument('--pool-per-tier',type=int,default=256)
    p.add_argument('--limit',type=int,default=48)
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None)->int:
    args=parser().parse_args(argv)
    if args.action=='train':
        print(ex.canon({'event':'t03_v21_train_complete',**train(args)}),flush=True)
    else:run_probe(args)
    return 0


if __name__=='__main__':raise SystemExit(main())
