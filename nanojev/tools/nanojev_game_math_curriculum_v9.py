#!/usr/bin/env python3
"""V9: seed-replayable V8 mathematical supervision, X/Y first, coupled Z diagnostic.

A NEW, ISOLATED training lineage. Existing T03 V2.1 checkpoints/source remain read-only.

    python -m tools.nanojev_game_math_curriculum_v9 build --native-db <v8.db>
    python -m tools.nanojev_game_math_curriculum_v9 probe --teacher-db <v9.db> ...
    python -m tools.nanojev_game_math_curriculum_v9 train --teacher-db <v9.db> ...

No oracle-derived X/Y/Z is included in a prompt unless its *tier grammar* exposes it.
No teacher answer is used as a neural prediction. Z is never used as a loss target.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
from typing import Any

from tools import nanojev_deterministic_brain_v8 as brain
from tools import nanojev_game_native_objectives_v6 as native
from tools import nanojev_game_transform_experiment as ex

SCHEMA = 'nanojev.game-math-xy-coupled-diagnostic.v9'
TIERS = tuple(f'T{i:02}' for i in range(9))
SUBLAYERS = ('T03a0','T03a1','T03a2','T03b0','T03b1','T03b2',
             'T03c','T03d','T03e','T03f','T03g','T03h','T03i')
LEVEL_ORDER = ('T00','T01','T02') + SUBLAYERS + ('T03','T04','T05','T06','T07','T08')
MATH_OPERATIONS = ('ADD','SUBTRACT','MULTIPLY','DIVIDE','MAX','ARGMAX')


def relative_error(pred: float, truth: float) -> float:
    """For |truth|<1, use absolute unit-scale tolerance; zero is well-defined."""
    return abs(float(pred)-float(truth))/max(1.,abs(float(truth)))


def within(pred: float, truth: float, tolerance: float=.05) -> bool:
    return math.isfinite(float(pred)) and relative_error(pred,truth)<=tolerance+1e-12


def number(value: Any) -> float:
    # V8 stores exact Fractions in tagged numerator/denominator dictionaries.
    if isinstance(value,dict) and set(value)=={'numerator','denominator'}:
        return float(Fraction(value['numerator'],value['denominator']))
    return float(native.mathlib.frac(value))


def coupled_z(pred_x:float, pred_y:float, operator:str, *, true_z:Any=None) -> dict:
    """Diagnostics operate on MODEL X/Y and TEACHER OP; never gold X/Y."""
    if operator not in MATH_OPERATIONS:
        raise ValueError('UNSUPPORTED_T00_OPERATOR:'+operator)
    if not (math.isfinite(pred_x) and math.isfinite(pred_y)):
        return {'status':'invalid_model_operands','prediction':None,'within_5pct':False}
    if operator=='DIVIDE' and abs(pred_y)<1e-10:
        return {'status':'predicted_zero_divisor','prediction':None,'within_5pct':False}
    try:
        if operator=='ARGMAX':
            z=native.mathlib.execute(operator,[native.num(pred_x),native.num(pred_y)])
        else:
            z=native.mathlib.execute(operator,native.num(pred_x),native.num(pred_y))
    except (ValueError,ArithmeticError,ZeroDivisionError,OverflowError):
        return {'status':'invalid_model_operation','prediction':None,'within_5pct':False}
    out={'status':'ok','prediction':native.mathlib.out(z)}
    if true_z is not None:
        out['within_5pct']=(int(z)==int(number(true_z))) if operator=='ARGMAX' else within(float(z),number(true_z))
        out['error']=abs(float(z)-number(true_z))
    return out


def deterministic_ladder(t08: str, mutation_seed:int, order_seed:int) -> dict:
    """A single V8 walk, retaining exactly the prompt after each local checkpoint."""
    st=brain.begin(t08)
    input_id=native.sha(st['prompt'])
    prompts={}; checkpoints=[]
    for tier in range(8,-1,-1):
        st,proof=brain.execute_checkpoint(st,order_seed=order_seed,
            mutation_seed=mutation_seed,input_id=input_id)
        if st['tier']!=tier:raise AssertionError('SKIPPED_TIER')
        prompts[f'T{tier:02}']=st['prompt']
        checkpoints.append({'tier':tier,'hash':proof['checkpoint_sha256'],
                            'order':proof['order']})
        if tier:st,_=brain.descend(st)
    answer=brain.coupled_t00(st,verify=True)  # Oracle only after T00.
    if answer['OP'] not in MATH_OPERATIONS:
        raise ValueError('MATH_OPERATION_NOT_SUPPORTED')
    return {'prompts':prompts,'answer':answer,'checkpoints':checkpoints,
            'input_sha256':input_id,'mutation_seed':mutation_seed,'order_seed':order_seed}


def stable_seed(*parts:Any)->int:
    digest=hashlib.sha256(ex.canon([SCHEMA,*parts]).encode()).digest()
    return int.from_bytes(digest[:7],'big')


def make_teacher(native_db:Path,output:Path,*,seed:int=20261017,
                 variants:int=1,limit_per_split:int|None=None)->dict:
    if variants<1 or limit_per_split is not None and limit_per_split<1:
        raise ValueError('INVALID_GENERATION_SIZE')
    src=native_db.resolve();dest=output.resolve()
    if not src.is_file() or src==dest:raise ValueError('SOURCE_DB_MISSING_OR_OVERWRITE')
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():raise FileExistsError('V9_DATABASE_IMMUTABLE_REBUILD_IN_NEW_DIRECTORY')
    rows=[];observed=defaultdict(int)
    with sqlite3.connect(f'file:{src.as_posix()}?mode=ro',uri=True) as db:
        for cid,split,game,objective,prompt in db.execute('''
            SELECT c.case_id,c.split,c.game_id,c.objective,t.prompt
            FROM native_v6_cases c JOIN native_v6_tiers t ON t.case_id=c.case_id AND t.tier=8
            ORDER BY c.split,c.game_id,c.case_id'''):
            if split not in ('train','dev','test','game_transfer'):continue
            if limit_per_split is not None and observed[split]>=limit_per_split:continue
            rows.append((cid,split,game,objective,prompt));observed[split]+=1
    with sqlite3.connect(dest) as db:
        db.executescript('''PRAGMA foreign_keys=ON;
        CREATE TABLE manifest(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE cases(
          run_id TEXT PRIMARY KEY,source_case_id TEXT NOT NULL,
          split TEXT NOT NULL,game_id TEXT NOT NULL,objective TEXT NOT NULL,
          mutation_seed INTEGER NOT NULL,order_seed INTEGER NOT NULL,
          input_sha256 TEXT NOT NULL,answer_json TEXT NOT NULL);
        CREATE TABLE prompts(run_id TEXT NOT NULL REFERENCES cases(run_id),
          level TEXT NOT NULL,prompt TEXT NOT NULL,PRIMARY KEY(run_id,level));
        CREATE INDEX by_split_level ON cases(split,game_id,objective);
        ''')
        db.executemany('INSERT INTO manifest VALUES (?,?)',[(k,str(v)) for k,v in
            {'schema':SCHEMA,'source_sha256':ex.filehash(src),'seed':seed,'variants':variants,
             'limit_per_split':limit_per_split if limit_per_split else 'all'}.items()])
        for cid,split,game,objective,prompt in rows:
            for variant in range(variants):
                mutation_seed=stable_seed(seed,split,cid,variant,'mutation')
                order_seed=stable_seed(seed,split,cid,variant,'order')
                record=deterministic_ladder(prompt,mutation_seed,order_seed)
                rid=native.sha([SCHEMA,cid,variant,mutation_seed,order_seed])
                db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?)',
                           (rid,cid,split,game,objective,mutation_seed,order_seed,
                            record['input_sha256'],native.canon(record['answer'])))
                db.executemany('INSERT INTO prompts VALUES (?,?,?)',
                               ((rid,level,record['prompts'][level]) for level in TIERS))
        db.commit()
        if db.execute('PRAGMA foreign_key_check').fetchall():raise AssertionError('INVALID_TEACHER_FK')
        counts=dict(db.execute('SELECT split,COUNT(*) FROM cases GROUP BY split'))
        n=db.execute('SELECT COUNT(*) FROM prompts').fetchone()[0]
        if n!=9*sum(counts.values()):raise AssertionError('MISSING_TIERS')
    return {'status':'PASS','cases':sum(counts.values()),'prompts':n,
            'splits':counts,'source_sha256':ex.filehash(src),'teacher_sha256':ex.filehash(dest)}


def read_cases(teacher_db:Path,split:str,limit:int|None=None)->list[dict]:
    if split not in ('train','dev','test','game_transfer'):raise ValueError('INVALID_SPLIT')
    with sqlite3.connect(f'file:{teacher_db.resolve().as_posix()}?mode=ro',uri=True) as db:
        sql='''SELECT c.run_id,c.game_id,c.objective,c.answer_json,p.level,p.prompt
          FROM cases c JOIN prompts p USING(run_id)
          WHERE c.split=? ORDER BY c.game_id,c.objective,c.run_id,p.level'''
        gathered={}
        for rid,gid,objective,answer,level,prompt in db.execute(sql,(split,)):
            if rid not in gathered:
                gathered[rid]={'id':rid,'game':gid,'objective':objective,
                               'answer':json.loads(answer),'prompts':{}}
            gathered[rid]['prompts'][level]=prompt
    by=list(gathered.values())
    if limit is not None:
        # Select whole cases, balanced by game/objective rather than first IDs.
        buckets=defaultdict(list)
        for row in by:buckets[(row['game'],row['objective'])].append(row)
        selected=[]
        while len(selected)<limit and buckets:
            for key in sorted(list(buckets)):
                if len(selected)>=limit:break
                selected.append(buckets[key].pop(0))
                if not buckets[key]:del buckets[key]
        by=selected
    return by


def native_records(rows:list[dict],level:str,predict,token_check=None)->list[dict]:
    records=[]
    for row in rows:
        answer=row['answer'];x=number(answer['X']);y=number(answer['Y'])
        prompt=row['prompts'][level]
        valid=token_check(prompt) if token_check is not None else True
        rec={'case_id':row['id'],'game':row['game'],'objective':row['objective'],
             'level':level,'truth_x':x,'truth_y':y,'true_OP':answer['OP'],
             'true_Z':answer['Z'],'status':'ok' if valid else 'unscorable'}
        if valid:
            try:
                px,py=predict(prompt)
                rec.update({'pred_x':float(px),'pred_y':float(py),
                            'x_ok':within(px,x),'y_ok':within(py,y),
                            'joint_ok':within(px,x) and within(py,y),
                            'coupled_Z':coupled_z(float(px),float(py),answer['OP'],true_z=answer['Z'])})
            except (ValueError,TypeError,ArithmeticError,OverflowError) as err:
                rec.update(status='invalid_prediction',error_type=type(err).__name__)
        records.append(rec)
    return records


def metrics(records:list[dict])->dict:
    n=len(records)
    if not n:return {'n':0,'x':0.,'y':0.,'joint_xy':0.,'coupled_Z':0.,'scorable':0}
    ok=[r for r in records if r['status']=='ok']
    # Unscorable are failures; excluding them would inflate the advancement gate.
    errs=[r for r in ok if all(k in r for k in ('pred_x','pred_y','truth_x','truth_y'))]
    return {'n':n,'x':sum(r.get('x_ok',False) for r in ok)/n,
            'y':sum(r.get('y_ok',False) for r in ok)/n,
            'joint_xy':sum(r.get('joint_ok',False) for r in ok)/n,
            'coupled_Z':sum(r['coupled_Z'].get('within_5pct',False) for r in ok)/n,
            'scorable':len(ok),
            'mean_x_relative_error':sum(relative_error(r['pred_x'],r['truth_x']) for r in errs)/len(errs) if errs else None,
            'mean_y_relative_error':sum(relative_error(r['pred_y'],r['truth_y']) for r in errs)/len(errs) if errs else None}


def gate(m:dict,*,minimum:float=.80,min_count:int=16)->dict:
    passed=(m['n']>=min_count and m['scorable']==m['n'] and
            all(m[k]>=minimum-1e-12 for k in ('x','y','joint_xy')))
    return {'passed':bool(passed),'minimum':minimum,'tolerance':.05,
            'minimum_cases':min_count,'x':m['x'],'y':m['y'],'joint_xy':m['joint_xy']}


def first_failing(scores:dict,*,minimum:float=.80,min_count:int=16)->str|None:
    for level in LEVEL_ORDER:
        if level not in scores:return level  # Unmeasured tier cannot be passed silently.
        if not gate(scores[level],minimum=minimum,min_count=min_count)['passed']:
            return level
    return None


def _native_level(level:str)->bool:return level in TIERS


def _legacy_items(level:str,seed:int,split:str,n:int)->list[dict]:
    from tools import nanojev_game_transform_t03_tiered_v21 as v21
    if level not in SUBLAYERS:raise ValueError('UNKNOWN_SUBLAYER')
    if level=='T03i':
        # T03i is a legacy source-data transfer tier; its heldout dataset
        # requires the separate MC3 source database and is never fabricated.
        raise ValueError('T03I_REQUIRES_LEGACY_SOURCE_TRANSFER_EVIDENCE')
    if level in v21.BRIDGE:
        return [v21.bridge_case(level,seed,split,i) for i in range(n)]
    return [v21.v1.oracle_case(level,seed,split,i) for i in range(n)]


def _legacy_prompt(level:str,row:dict,orientation:str,model)->str:
    from tools import nanojev_game_transform_t03_tiered_v21 as v21
    if level in v21.BRIDGE:
        return v21.bridge_prompt(level,row,orientation,model['mix'],model['base'])
    return v21.v1.tier_prompt(level,row,orientation)


def probe(model,teacher_db:Path,*,seed:int=20261017,split:str='dev',
          limit:int=48,include_sublayers:bool=True,legacy_db:Path|None=None)->dict:
    from tools import nanojev_game_transform_level_probe as older_probe
    rows=read_cases(teacher_db,split,limit)
    if not rows:raise RuntimeError('NO_HELDOUT_NATIVE_CASES')
    def predict(prompt):return ex.predict_one(model,prompt)[0]
    def token_ok(prompt):return older_probe.token_check(model['tokenizer'],prompt,model['config']['max_prompt_tokens'])['status']=='ok'
    scores={};examples={};by_operation={}
    for level in TIERS:
        records=native_records(rows,level,predict,token_ok)
        scores[level]=metrics(records)
        by_operation[level]={op:metrics([r for r in records if r['true_OP']==op])
                              for op in MATH_OPERATIONS if any(r['true_OP']==op for r in records)}
        examples[level]=records[:3]
    if include_sublayers:
        from tools import nanojev_game_transform_t03_tiered_v21 as v21
        for level in SUBLAYERS:
            if level=='T03i':
                if legacy_db is None or not legacy_db.is_file():
                    scores[level]={'n':0,'scorable':0,'x':0.,'y':0.,'joint_xy':0.,'coupled_Z':None}
                    continue
                from tools import nanojev_game_transform_ladder_map as ladder
                with sqlite3.connect(f'file:{legacy_db.resolve().as_posix()}?mode=ro',uri=True) as db:
                    available=ex.rows_for(db,'game_transfer')
                    chosen=older_probe.select_balanced(available,limit)
                    items=[r for r in ladder.materialize_source(db,chosen) if older_probe.available_integer(r) and r['x']!=r['y']]
                if not items:
                    scores[level]={'n':0,'scorable':0,'x':0.,'y':0.,'joint_xy':0.,'coupled_Z':None}
                    continue
            else:
                items=_legacy_items(level,seed,split,limit)
            records=[]
            for row in items:
                for orientation in ('forward','reordered'):
                    prompt=(older_probe.render('T03',row,orientation,model['mix'],model['base'])
                            if level=='T03i' else _legacy_prompt(level,row,orientation,model))
                    valid=token_ok(prompt)
                    rec={'case_id':row['id'],'level':level,'status':'ok' if valid else 'unscorable',
                         'truth_x':float(row['x']),'truth_y':float(row['y'])}
                    if valid:
                        try:
                            px,py=predict(prompt)
                            rec.update(x_ok=within(px,row['x']),y_ok=within(py,row['y']),
                                       joint_ok=within(px,row['x']) and within(py,row['y']),
                                       coupled_Z=coupled_z(float(px),float(py),'SUBTRACT',
                                                           true_z=native.mathlib.out(native.num(row['x'])-native.num(row['y']))))
                        except (ValueError,TypeError,ArithmeticError,OverflowError):
                            rec['status']='invalid_prediction'
                    records.append(rec)
            m=metrics_xy_only(records)
            scores[level]=m
            examples[level]=records[:2]
    status=first_failing(scores)
    return {'schema':SCHEMA,'split':split,'tolerance':.05,'minimum_success_fraction':.8,
            'scores':scores,'by_operation':by_operation,'first_failing_level':status,
            'unprobed_levels':[x for x in LEVEL_ORDER if x not in scores],
            'examples':examples}


def metrics_xy_only(records:list[dict])->dict:
    n=len(records);ok=[r for r in records if r['status']=='ok']
    return {'n':n,'scorable':len(ok),
            'x':sum(r.get('x_ok',False) for r in ok)/n if n else 0.,
            'y':sum(r.get('y_ok',False) for r in ok)/n if n else 0.,
            'joint_xy':sum(r.get('joint_ok',False) for r in ok)/n if n else 0.,
            'coupled_Z':sum(r['coupled_Z'].get('within_5pct',False) for r in ok)/n if n else 0.}  # Fixed legacy SUBTRACT operation.


def _load_model(args):
    import torch
    model=ex.load_current(args.source_checkpoint.resolve(),args.device,args.dtype,
                          args.allow_model_download,args.seed)
    cp=torch.load(args.parent_checkpoint.resolve(),map_location='cpu',weights_only=True)
    if cp.get('source_sha256')!=ex.filehash(args.source_checkpoint.resolve()):
        raise RuntimeError('PARENT_SOURCE_CHECKPOINT_MISMATCH')
    if args.legacy_db and cp.get('database_sha256')!=ex.filehash(args.legacy_db.resolve()):
        raise RuntimeError('PARENT_LEGACY_DATABASE_MISMATCH')
    model['decoder'].load_state_dict(cp['decoder'],strict=True)
    model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
    override=getattr(args,'experimental_checkpoint',None)
    if getattr(args,'action',None)=='probe' and override:
        other=torch.load(override.resolve(),map_location='cpu',weights_only=True)
        if other.get('schema')!=SCHEMA or other.get('config')!=_config(args):
            raise RuntimeError('V9_PROBE_CHECKPOINT_PROVENANCE_DRIFT')
        model['decoder'].load_state_dict(other['decoder'],strict=True)
        model['gamehead'].load_state_dict(other['game_operator_head'],strict=True)
    return model,cp


def _config(args):
    return {'schema':SCHEMA,'teacher_sha256':ex.filehash(args.teacher_db.resolve()),
            'source_sha256':ex.filehash(args.source_checkpoint.resolve()),
            'parent_sha256':ex.filehash(args.parent_checkpoint.resolve()),
            'legacy_db_sha256':ex.filehash(args.legacy_db.resolve()) if args.legacy_db and args.legacy_db.exists() else None,
            'seed':args.seed,'lr':args.lr,'chunk_steps':args.chunk_steps,
            'batch_levels':args.batch_levels,'min_count':args.min_count,
            'gate_fraction':args.gate_fraction,'generator_sha256':ex.filehash(Path(__file__))}


def train(args):
    """Continue X/Y decoder in isolated V9 lineage; native Z has ZERO loss weight."""
    import torch
    if args.skip_sublayers:
        raise ValueError('TRAINING_CANNOT_SKIP_REQUIRED_SUBLAYER_GATES')
    if args.batch_levels<3 or args.chunk_steps<1 or not (0<args.gate_fraction<=1):
        raise ValueError('BAD_TRAINING_SCHEDULE')
    model,parent=_load_model(args)
    teacher=args.teacher_db.resolve()
    train_rows=read_cases(teacher,'train')
    if not train_rows:raise RuntimeError('NO_NATIVE_TRAINING_CASES')
    from tools import nanojev_game_transform_level_probe as older_probe
    token_ok=lambda text:older_probe.token_check(model['tokenizer'],text,model['config']['max_prompt_tokens'])['status']=='ok'
    native_pools={level:[row for row in train_rows if token_ok(row['prompts'][level])]
                  for level in TIERS}
    native_coverage={level:{'eligible':len(native_pools[level]),'total':len(train_rows)} for level in TIERS}
    print(ex.canon({'event':'v9_native_token_preflight','coverage':native_coverage}),flush=True)
    if not native_pools['T00']:
        raise RuntimeError('NO_T00_TASKS_FIT_CURRENT_FROZEN_BACKBONE_TOKEN_LIMIT')
    if args.legacy_db is None or not args.legacy_db.is_file():
        raise FileNotFoundError('LEGACY_DATABASE_REQUIRED_FOR_T03_SUBLAYERS')
    dest=args.output.resolve()
    if dest==args.parent_checkpoint.resolve().parent or dest==args.source_checkpoint.resolve().parent:
        raise ValueError('MUST_NOT_OVERWRITE_PARENT')
    dest.mkdir(parents=True,exist_ok=True)
    config=_config(args);config_path=dest/'v9-config.json'
    if config_path.exists():
        if json.loads(config_path.read_text())!=config:raise RuntimeError('V9_CONFIG_DRIFT')
    else:ex.write_json(config_path,config)
    decoder=model['decoder'];gamehead=model['gamehead']
    for p in gamehead.parameters():p.requires_grad_(False)
    optimizer=torch.optim.AdamW(decoder.parameters(),lr=args.lr)
    cp_path=dest/'latest.pt'
    if cp_path.exists():
        cp=torch.load(cp_path,map_location='cpu',weights_only=True)
        if cp.get('schema')!=SCHEMA or cp.get('config')!=config:raise RuntimeError('V9_RESUME_DRIFT')
        decoder.load_state_dict(cp['decoder'],strict=True)
        optimizer.load_state_dict(cp['optimizer'])
        torch.set_rng_state(cp['rng_cpu'])
        if args.device=='cuda' and cp.get('rng_cuda'):torch.cuda.set_rng_state_all(cp['rng_cuda'])
        step=cp['step'];focus=cp['focus'];passes=cp['passes']
    else:
        step=int(parent['step']);focus='T00';passes=0
    if args.steps<=step:raise ValueError('TARGET_MUST_EXCEED_STEP')
    while step<args.steps:
        cutoff=min(step+args.chunk_steps,args.steps)
        for target in range(step+1,cutoff+1):
            decoder.train();optimizer.zero_grad(set_to_none=True)
            rng=random.Random(stable_seed(args.seed,target,'train-batch'))
            # Focus most examples on the earliest failing tier, retain earlier
            # native skills, and keep legacy T03 sublayers in the mix.
            earlier=list(LEVEL_ORDER[:LEVEL_ORDER.index(focus)])
            tasks=[focus]*max(1,args.batch_levels-2)+[rng.choice(earlier or ['T00'])]
            if args.batch_levels>=3:tasks.append(rng.choice(('T03a0','T03a1','T03b0')))
            tasks=tasks[:args.batch_levels];rng.shuffle(tasks)
            objective_loss=0.
            for slot,level in enumerate(tasks):
                if _native_level(level):
                    eligible=native_pools[level]
                    if not eligible:
                        raise RuntimeError('NO_NATIVE_PROMPTS_WITHIN_TOKEN_LIMIT:'+level)
                    row=eligible[stable_seed(args.seed,target,slot,level)%len(eligible)]
                    prompt=row['prompts'][level];answer=row['answer']
                    x,y=number(answer['X']),number(answer['Y'])
                else:
                    from tools import nanojev_game_transform_t03_tiered_v21 as v21
                    if level=='T03i':
                        from tools import nanojev_game_transform_ladder_map as ladder
                        from tools import nanojev_game_transform_level_probe as level_probe
                        with sqlite3.connect(f'file:{args.legacy_db.resolve().as_posix()}?mode=ro',uri=True) as db:
                            candidates=[r for r in ex.rows_for(db,'train') if level_probe.available_integer(r) and r['x']!=r['y']]
                            if not candidates:raise RuntimeError('T03I_NO_TRAIN_CASES')
                            row=ladder.materialize_source(db,[candidates[stable_seed(args.seed,target,slot)%len(candidates)]])[0]
                        prompt=level_probe.render('T03',row,rng.choice(('forward','reordered')),model['mix'],model['base'])
                    else:
                        row=(v21.bridge_case(level,args.seed,'train',target+slot)
                             if level in v21.BRIDGE else v21.v1.oracle_case(level,args.seed,'train',target+slot))
                        prompt=_legacy_prompt(level,row,rng.choice(('forward','reordered')),model)
                    x,y=float(row['x']),float(row['y'])
                if older_probe.token_check(model['tokenizer'],prompt,
                      model['config']['max_prompt_tokens'])['status']!='ok':
                    raise RuntimeError('TRAINING_PROMPT_UNSCORABLE:'+level)
                output,_=ex.forward(model,[prompt])
                gold=torch.tensor([[x,y]],device=args.device,dtype=torch.float32)
                loss,_=model['v3'].objective(output,gold,model['base'],'attention')
                (loss/len(tasks)).backward()
                objective_loss+=float(loss.detach())/len(tasks)
            torch.nn.utils.clip_grad_norm_(decoder.parameters(),1.)
            optimizer.step()
        step=cutoff
        decoder.eval();gamehead.eval()
        with torch.inference_mode():
            report=probe(model,teacher,seed=args.seed,split='dev',
                         limit=args.probe_limit,include_sublayers=not args.skip_sublayers,legacy_db=args.legacy_db)
        report['step']=step;report['training_loss']=objective_loss
        first=first_failing(report['scores'],minimum=args.gate_fraction,min_count=args.min_count)
        # Do not leap past unmeasured or failed lower tiers. Require two
        # consecutive successful heldout probes for promotion.
        desired=first if first in LEVEL_ORDER else 'T08'
        if first is not None and LEVEL_ORDER.index(first)<LEVEL_ORDER.index(focus):
            focus=first  # Regressions at previously completed tiers reclaim budget.
            passes=0
        elif first is None or LEVEL_ORDER.index(first)>LEVEL_ORDER.index(focus):
            passes+=1
        else:passes=0
        if passes>=2 and LEVEL_ORDER.index(desired)>LEVEL_ORDER.index(focus):
            focus=desired;passes=0
        report['focus']=focus;report['consecutive_passes']=passes
        ex.write_json(dest/f'probe-step-{step:09d}.json',report)
        saved={'schema':SCHEMA,'config':config,'step':step,'focus':focus,'passes':passes,
               'decoder':decoder.state_dict(),'game_operator_head':gamehead.state_dict(),
               'optimizer':optimizer.state_dict(),'rng_cpu':torch.get_rng_state()}
        if args.device=='cuda':saved['rng_cuda']=torch.cuda.get_rng_state_all()
        tmp=dest/'latest.tmp.pt';torch.save(saved,tmp);tmp.replace(cp_path)
        print(ex.canon({'event':'v9_xy_probe','step':step,'focus':focus,
                        'first_failing':first,'native_Z_diagnostic_only':True,
                        'report':str(dest/f'probe-step-{step:09d}.json')}),flush=True)
    return {'status':'PASS','step':step,'focus':focus,'checkpoint':str(cp_path),
            'new_lineage':True,'Z_loss_weight':0}


def cli(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('build','probe','train'))
    p.add_argument('--native-db',type=Path,default=Path('runtime/game-math-source-smoke-v8/game_transformations.sqlite3'))
    p.add_argument('--teacher-db',type=Path,default=Path('runtime/game-math-curriculum-v9/teacher.sqlite3'))
    p.add_argument('--source-checkpoint',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--parent-checkpoint',type=Path,default=Path.home()/'NanoJev/runs/game_transform_t03_tiered_v21/stage-00/latest.pt')
    p.add_argument('--experimental-checkpoint',type=Path,help='Probe a saved V9 checkpoint instead of the V2.1 parent')
    p.add_argument('--legacy-db',type=Path,default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
    p.add_argument('--output',type=Path,default=Path.home()/'NanoJev/runs/game_math_xy_v9')
    p.add_argument('--seed',type=int,default=20261017)
    p.add_argument('--order-seed',type=int,default=11)
    p.add_argument('--variants',type=int,default=1)
    p.add_argument('--limit-per-split',type=int)
    p.add_argument('--split',choices=('dev','test'),default='dev')
    p.add_argument('--probe-limit',type=int,default=48)
    p.add_argument('--gate-fraction',type=float,default=.8)
    p.add_argument('--min-count',type=int,default=16)
    p.add_argument('--steps',type=int,default=221000)
    p.add_argument('--chunk-steps',type=int,default=250)
    p.add_argument('--batch-levels',type=int,default=4)
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--skip-sublayers',action='store_true',help='Probe native tiers only (training retains legacy sublayers)')
    args=p.parse_args(argv)
    if args.action=='build':result=make_teacher(args.native_db,args.teacher_db,seed=args.seed,
                                                 variants=args.variants,limit_per_split=args.limit_per_split)
    elif args.action=='train':result=train(args)
    else:
        import torch
        model,_=_load_model(args)
        model['decoder'].eval();model['gamehead'].eval()
        with torch.inference_mode():
            result=probe(model,args.teacher_db,seed=args.seed,split=args.split,
                         limit=args.probe_limit,include_sublayers=not args.skip_sublayers,legacy_db=args.legacy_db)
        result['model_checkpoint_sha256']=ex.filehash(args.experimental_checkpoint or args.parent_checkpoint)
        result['teacher_sha256']=ex.filehash(args.teacher_db)
        args.output.mkdir(parents=True,exist_ok=True)
        ex.write_json(args.output/'probe-v9.json',result)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0


if __name__=='__main__':raise SystemExit(cli())
