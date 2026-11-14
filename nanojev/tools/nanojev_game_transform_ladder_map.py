#!/usr/bin/env python3
"""Diagnostic-first, explicitly weighted NanoJev game-transformation ladder.

Source database and original V4/MC3 checkpoints are always read-only. The user
chooses the named weight-map phase; this command NEVER advances phases by step.
SQL teacher values are used as labels only, never inserted into hidden fields.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import time
import os
from types import SimpleNamespace

from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_transform_level_probe as probe

SCHEMA = 'nanojev.game-transform-ladder-map.v1'
CACHE_SCHEMA = 'nanojev.game-transform-frozen-capture-cache.v2'
TRAINER_VERSION = 'numerical-interference-v2-batched-cache'
CONTROL_LEVELS = ('T00','T01','T02a','T02b','T02c','T02d','T02e','T02f','T02g','T02h','T02i','T02j','T02k','T02n')
LEVELS = CONTROL_LEVELS + ('T03','T04','T05','T06','T07','T08')
TRAINABLE = tuple(x for x in LEVELS if x != 'T08') + ('retention',)
DIAG_LEVELS = CONTROL_LEVELS


def read_map(path: Path, phase: str) -> tuple[dict,dict]:
    spec=json.loads(path.read_text(encoding='utf-8'))
    if spec.get('schema') != SCHEMA or not isinstance(spec.get('phases'),dict):
        raise ValueError('INVALID_LADDER_MAP_SCHEMA')
    if phase not in spec['phases']:raise ValueError('UNKNOWN_MAP_PHASE:'+phase)
    raw=spec['phases'][phase]
    if not isinstance(raw,dict):raise ValueError('PHASE_MUST_BE_A_MAP')
    for name, n in raw.items():
        if name not in TRAINABLE or type(n) is not int or n<0:
            raise ValueError('INVALID_LEVEL_QUOTA:'+str(name))
    quotas={key:val for key,val in raw.items() if val}
    if quotas.get('retention',0)<1 or not any(k!='retention' for k in quotas):
        raise ValueError('MAP_REQUIRES_RETENTION_AND_LADDER_CASES')
    if sum(quotas.values())>128:raise ValueError('LEVEL_MAP_EXCEEDS_128_LOGICAL_SLOTS')
    return spec,quotas


def materialize_source(cn:sqlite3.Connection,rows:list[dict])->list[dict]:
    """Only SOURCE structure. Never load target_json into inference prompts."""
    sources=dict(cn.execute('SELECT case_id,input_json FROM transformation_cases'))
    result=[]
    for row in rows:
        out=dict(row)
        out['source_structure']=json.loads(sources[row['id']])
        result.append(out)
    return result


def full_context(row:dict)->str:
    return row['forward'].rsplit('\nAnswer:',1)[0]


def contextual_prompt(level:str,row:dict,orientation:str,mix,base)->str:
    """T02 variants isolate *one* addition to exactly preserved native text."""
    if level not in LEVELS:raise ValueError('UNKNOWN_LADDER_LEVEL:'+level)
    if level in ('T00','T01','T03','T04','T05','T06','T07','T08'):
        return probe.render(level,row,orientation,mix,base)
    if not probe.available_integer(row):
        raise probe.UnsupportedNumericNative('NATIVE_V4_IS_INTEGER_ONLY')
    native=probe.native_prompt(mix,base,row,orientation=='reordered')
    src=row.get('source_structure')
    if src is None:
        raise ValueError('MISSING_SOURCE_STRUCTURE_FOR_T02')
    if level=='T02a':
        return 'Game: '+row['game']+'.\n'+native
    if level=='T02k':
        return probe.insert_before_answer(native,'Game: '+row['game']+'.')
    if level=='T02b':
        # Text identical to native T01's transform label, but *prefixed*.
        # T01 vs T02b therefore isolates placement, not changed grammar.
        return 'Transformation label: '+row['operator']+'.\n'+native
    if level in ('T02c','T02j'):
        # Deliberately only ONE unrelated number; avoid row/column coordinate
        # indices and extra payoff cells from the old T02c implementation.
        value=7+(int(hashlib.sha256(row['id'].encode()).hexdigest()[:8],16)%83)
        while value in (row['x'],row['y']): value+=1
        context='Auxiliary observation: '+str(value)+'.'
        return context+'\n'+native if level=='T02c' else probe.insert_before_answer(native,context)
    if level in ('T02h','T02i'):
        if 'U' in src:
            one,two=src['U'][0][0]
        else:
            params=src.get('parameters',{})
            values=[params[k] for k in sorted(params)]
            one=values[0] if values else 0
            two=values[1] if len(values)>1 else one
        context=('Source payoff: '+ex.canon(one)+'.' if level=='T02h' else
                 'Source payoff pair: '+ex.canon(one)+', '+ex.canon(two)+'.')
        return context+'\n'+native
    if level=='T02d':
        rule=ex.mathlib.TRANSFORM_LOOKUP.get(row['operator'],'')
        return 'Transformation definition: '+str(rule)+'.\n'+native
    if level=='T02e':
        data=src['U'] if 'U' in src else src.get('parameters',{})
        return 'Source game structure: '+ex.canon(data)+'.\n'+native
    if level=='T02f':
        # Deliberate reproduction of the original failing T02 prompt.
        return full_context(row)+'\n'+native
    if level=='T02g':
        # Exact same formal words as T02f, after registers but before operation.
        marker='\noperation:'
        if marker not in native:raise AssertionError('NATIVE_OPERATION_NOT_FOUND')
        return native.replace(marker,'\n'+full_context(row)+marker,1)
    if level=='T02n':
        # Neutral length control; token counts are always recorded separately.
        words='Background observation without additional numbers or commands. '
        filler=(words*max(1,round(len(full_context(row))/len(words)))).rstrip()
        return filler+'\n'+native
    raise AssertionError(level)


def contexts(db:Path,limit:int,include_unchanged:bool=False)->dict[str,list[dict]]:
    if limit<1:raise ValueError('LIMIT_MUST_BE_POSITIVE')
    with closing(sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True)) as cn:
        return {split:materialize_source(cn,probe.select_balanced(ex.rows_for(cn,split),limit,
                     changed_only=not include_unchanged)) for split in ('dev','test','game_transfer')}


def infer_level(model,items:list[dict],level:str,split:str)->tuple[list[dict],dict]:
    output=[]
    for item in items:
        for orientation in ('forward','reordered'):
            try:prompt=contextual_prompt(level,item,orientation,model['mix'],model['base'])
            except probe.UnsupportedNumericNative:prompt=None
            validity=(probe.token_check(model['tokenizer'],prompt,model['config']['max_prompt_tokens'])
                      if prompt is not None else {'status':'unsupported_noninteger_native','tokens':None})
            record={'case_id':item['id'],'game':item['game'],'operator':item['operator'],
                    'split':split,'level':level,'orientation':orientation,
                    'truth':{'x':item['x'],'y':item['y']},'status':validity['status'],
                    'tokens':validity['tokens'],
                    'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest() if prompt else None}
            if validity['status']=='ok':
                xy,op=ex.predict_one(model,prompt)
                record.update(prediction=xy,predicted_operator=op)
            output.append(record)
    five=probe.score(output,level,.05)
    ten=probe.score(output,level,.10)
    return output,{'n_selected':len(items),'5pct':five,'10pct':ten}


def check_input_hashes(paths:dict[str,Path],sha:dict):
    for k,p in paths.items():
        if ex.filehash(p)!=sha[k]:raise RuntimeError('READ_ONLY_SOURCE_MODIFIED:'+k)


def check_diagnostic(report:Path,db:Path,source:Path)->dict:
    if not report.is_file():raise FileNotFoundError('DIAGNOSTIC_REPORT_REQUIRED')
    data=json.loads(report.read_text(encoding='utf8'))
    if data.get('schema')!=SCHEMA or data.get('database_sha256')!=ex.filehash(db) or \
       data.get('source_checkpoint_sha256')!=ex.filehash(source) or \
       data.get('generator_sha256')!=ex.filehash(Path(__file__)):
        raise RuntimeError('DIAGNOSTIC_IDENTITY_MISMATCH')
    if not {'T00','T02a','T02e','T02f','T02g','T02n'}.issubset(set(data.get('levels',[]))):
        raise RuntimeError('DIAGNOSTIC_MISSING_T02_ISOLATION')
    return data


def diagnose(args):
    db=args.database.resolve();source=args.source_checkpoint.resolve()
    exp=args.experimental_checkpoint.resolve() if args.experimental_checkpoint else None
    files={'database':db,'source':source}
    if exp:files['experimental']=exp
    if not all(x.is_file() for x in files.values()):raise FileNotFoundError('CHECKPOINT_OR_DB_MISSING')
    hashes={k:ex.filehash(v) for k,v in files.items()}
    chosen=contexts(db,args.limit_per_split,args.include_unchanged)
    levels=[x.strip() for x in args.levels.split(',') if x.strip()]
    if not levels or any(x not in LEVELS for x in levels):raise ValueError('INVALID_DIAGNOSTIC_LEVELS')
    if len(set(levels))!=len(levels):raise ValueError('DUPLICATE_LEVELS')
    report={'schema':SCHEMA,'generator_sha256':ex.filehash(Path(__file__)),
            'database_sha256':hashes['database'],
            'source_checkpoint_sha256':hashes['source'],
            'experimental_checkpoint_sha256':hashes.get('experimental'),
            'levels':levels,'input_case_ids':{s:[x['id'] for x in items] for s,items in chosen.items()},
            'models':{}}
    for name,path in [('source',source)]+([('experimental',exp)] if exp else []):
        model=ex.load_current(source,args.device,args.dtype,args.allow_model_download,args.seed)
        step=model['source']['step']
        if name=='experimental':
            cp=model['torch'].load(exp,map_location='cpu',weights_only=True)
            if cp.get('schema')==ex.SCHEMA:
                # Verify source/DB provenance; never silently compare unrelated lineages.
                config=exp.parent/'experiment-config.json'
                probe.identity(source,exp,db,config if config.exists() else None)
                model['decoder'].load_state_dict(cp['decoder'],strict=True)
                model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
            elif cp.get('schema')==SCHEMA:
                if cp.get('source_sha256')!=hashes['source'] or cp.get('database_sha256')!=hashes['database']:
                    raise RuntimeError('LADDER_EXPERIMENT_LINEAGE_DRIFT')
                model['decoder'].load_state_dict(cp['decoder'],strict=True)
                model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
            else:raise RuntimeError('NOT_A_GAME_EXPERIMENT_CHECKPOINT')
            step=cp['step']
        model['decoder'].eval();model['gamehead'].eval()
        scores={};rows=[]
        with model['torch'].inference_mode():
            for split,items in chosen.items():
                for level in levels:
                    use=[x for x in items if x['operator']=='compose_transformations'] if level=='T07' else items
                    record,metrics=infer_level(model,use,level,split)
                    scores[f'{split}/{level}']=metrics
                    rows+=record
        report['models'][name]={'step':step,'checkpoint_sha256':hashes[name],
                                'summary':scores,'rows':rows}
        framework=model['torch'];del model
        if args.device=='cuda':framework.cuda.empty_cache()
    check_input_hashes(files,hashes)
    if args.report:
        if args.report.resolve() in {p.resolve() for p in files.values()}:
            raise ValueError('REPORT_WOULD_OVERWRITE_SOURCE')
        ex.write_json(args.report,report)
    summary={name:{k:{'n':v['5pct']['n'],'paired5':v['5pct']['paired']['joint'],
                     'paired10':v['10pct']['paired']['joint'],
                     'x5':v['5pct']['paired']['x'],'y5':v['5pct']['paired']['y'],
                     'op':v['5pct']['operator_paired'],
                     'unscorable':v['5pct']['unscorable'],
                     'mean_tokens':round(sum(r['tokens'] or 0 for r in m['rows'] if r['split']+'/'+r['level']==k)/
                           max(1,sum(1 for r in m['rows'] if r['split']+'/'+r['level']==k and r['tokens'] is not None)),1)}
                         for k,v in m['summary'].items()}
              for name,m in report['models'].items()}
    print(ex.canon({'event':'ladder_t02_diagnostic_complete','report':str(args.report) if args.report else None,
                    'summary':summary}),flush=True)
    return report


def eligible_pools(db:Path,model,quota:dict)->dict[str,list[dict]]:
    with closing(sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True)) as cn:
        pool=materialize_source(cn,ex.rows_for(cn,'train'))
    eligible={}
    for level in quota:
        if level=='retention':continue
        candidates=[]
        for row in pool:
            if level in CONTROL_LEVELS+('T03','T04') and not probe.available_integer(row):continue
            if level in ('T03','T04','T05','T06','T07') and row['x']==row['y']:continue
            if level=='T07' and row['operator']!='compose_transformations':continue
            try:
                texts=[contextual_prompt(level,row,ori,model['mix'],model['base']) for ori in ('forward','reordered')]
            except probe.UnsupportedNumericNative:continue
            if all(probe.token_check(model['tokenizer'],t,model['config']['max_prompt_tokens'])['status']=='ok' for t in texts):
                candidates.append(row)
        if not candidates:raise RuntimeError('NO_SCORABLE_TRAINING_CASES_FOR_LEVEL:'+level)
        eligible[level]=probe.select_balanced(candidates,len(candidates))
    return eligible


def _cache_identity(model, sha, map_hash, args):
    return {'schema':CACHE_SCHEMA,'trainer_version':TRAINER_VERSION,
            'database_sha256':sha['database'],'source_sha256':sha['source'],
            'map_sha256':map_hash,'seed':args.seed,
            'cache_window':args.cache_window,'refresh_every':args.refresh_every,
            'cache_per_level':args.cache_per_level,'retention_cache':args.retention_cache,
          'refresh_every':args.refresh_every,
            'max_prompt_tokens':model['config']['max_prompt_tokens'],
            'levels':sorted(args.quotas)}


def _capture(model,prompt):
    '''Compute Qwen representations ONCE; all tensors remain detached.'''
    torch=model['torch'];v3=model['v3'];device=model['device']
    with torch.no_grad():
        flat,seq,mask=v3.full_capture(model['backbone'],model['tokenizer'],device,
                                       [prompt],model['config']['max_prompt_tokens'])
        h=model['parent'].extract(flat.to(device))
        xy=model['parent'].xy_coord(h)
    return {'h':h[0].detach().to('cpu',dtype=torch.float32).clone(),
            'pxy':xy[0].detach().to('cpu',dtype=torch.float32).clone(),
            'seq':seq[0,:int(mask[0].sum())].detach().to('cpu',dtype=torch.float16).clone()}


def _cache_create(model,pools,quotas,legacy,stream,args):
    '''Deterministic capped bank; no dev/test or withheld games admitted.'''
    cases={};retention=[]
    for level in sorted(pools):
        # Window rotation reaches different training-only cases over time.
        # eligible_pools already gives a deterministic balanced ordering.
        source=pools[level];count=min(len(source),args.cache_per_level)
        offset=(args.cache_window*count)%len(source)
        chosen=[source[(offset+i)%len(source)] for i in range(count)]
        examples=[]
        for idx,case in enumerate(chosen):
            views=[]
            for ori in ('forward','reordered'):
                prompt=contextual_prompt(level,case,ori,model['mix'],model['base'])
                views.append(_capture(model,prompt))
            examples.append({'id':case['id'],'x':case['x'],'y':case['y'],
                             'operator':case['operator'],'views':views})
        cases[level]=examples
        print(ex.canon({'event':'ladder_cache_level_ready','level':level,
                         'cached_cases':len(examples)}),flush=True)
    # Retention uses a fixed, separate training-only sample; the stream may
    # advance during cache construction but its persisted RNG is NOT used.
    # Build independent cache stream to avoid perturbing checkpoint resume.
    rstream=model['base'].Stream(args.seed+707,stream.reserved)
    for i in range(args.retention_cache):
        e=rstream.get()
        prompt=model['mix'].curriculum_prompt(e['a'],e['b'],e['operation'],
                                              'registered_logbar',False)
        retention.append({'a':e['a'],'b':e['b'],
                          'operation':e['operation'],'view':_capture(model,prompt)})
    return {'levels':cases,'retention':retention}


def _get_feature_cache(model,pools,quotas,stream,args,out,sha,map_hash):
    torch=model['torch']
    path=out/('frozen-capture-cache-'+args.phase+'.pt')
    identity=_cache_identity(model,sha,map_hash,args)
    if path.exists():
        data=torch.load(path,map_location='cpu',weights_only=True)
        if data.get('identity')!=identity:
            cache=None
        else:
            cache=data['data']
        if cache is not None:
            for lvl in sorted(pools):
                if not cache['levels'].get(lvl):raise RuntimeError('FROZEN_CAPTURE_CACHE_MISSING_LEVEL:'+lvl)
            print(ex.canon({'event':'ladder_cache_reused','path':str(path),
                            'window':args.cache_window}),flush=True)
    else:
        cache=None
    if cache is None:
        cache=_cache_create(model,pools,quotas,None,stream,args)
        tmp=out/('frozen-capture-cache-'+args.phase+'-tmp.pt')
        torch.save({'identity':identity,'data':cache},tmp)
        os.replace(tmp,path)
        print(ex.canon({'event':'ladder_cache_saved','path':str(path),
                        'size_bytes':path.stat().st_size}),flush=True)
    return cache


def _bank_select(bank,step,slot,seed):
    # Coprime stride is calculated for each level-specific capped bank.
    import math
    stride=17
    while math.gcd(stride,len(bank))!=1:stride+=1
    return bank[(seed+step*stride+slot*37)%len(bank)]


def _batch_loss(model,examples,op_weight):
    torch=model['torch'];nn=model['v3'];base=model['base'];F=model['F']
    device=model['device'];n=len(examples)
    maxlen=max(len(x['feature']['seq']) for x in examples)
    dim=examples[0]['feature']['seq'].shape[-1]
    seq=torch.zeros((n,maxlen,dim),device=device,dtype=torch.float32)
    mask=torch.zeros((n,maxlen),device=device,dtype=torch.bool)
    hs=[];pxys=[]
    for j,item in enumerate(examples):
        f=item['feature'];length=len(f['seq'])
        seq[j,:length]=f['seq'].to(device,dtype=torch.float32)
        mask[j,:length]=True
        hs.append(f['h']);pxys.append(f['pxy'])
    h=torch.stack(hs).to(device)
    pxy=torch.stack(pxys).to(device)
    ops=torch.full((n,),base.OP_IDS['written_sub'],device=device,dtype=torch.long)
    outputs=model['decoder'](h,pxy,ops,seq,mask)
    truth=torch.tensor([[x['x'],x['y']] for x in examples],device=device,dtype=torch.float32)
    xy_loss,_=nn.objective(outputs,truth,base,'attention')
    # Classifier is supervised only on genuine mathematical transformation
    # examples, not on retention examples with no game transformation.
    op_rows=[i for i,x in enumerate(examples) if x['operator'] is not None]
    if op_rows:
        logits=model['gamehead'](seq[op_rows],mask[op_rows])
        labels=torch.tensor([ex.ALL_OPERATORS.index(examples[i]['operator'])
                             for i in op_rows],device=device,dtype=torch.long)
        op_loss=F.cross_entropy(logits,labels)
        loss=xy_loss+op_weight*op_loss
    else:loss=xy_loss
    if not bool(torch.isfinite(loss)):
        raise RuntimeError('NONFINITE_LADDER_BATCH_LOSS')
    return loss


def train(args):
    torch,nn,F,mix,v3,base=ex.torch_runtime()
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA_UNAVAILABLE')
    if args.steps<1 or args.save_every<1 or args.report_every<1 or args.cache_per_level<1 or args.retention_cache<1:
        raise ValueError('INVALID_TRAINING_LIMIT')
    if args.max_wall_hours<0 or args.refresh_every<1:raise ValueError('INVALID_WALL_OR_REFRESH')
    db=args.database.resolve();src=args.source_checkpoint.resolve()
    if not db.is_file() or not src.is_file():raise FileNotFoundError('DATABASE_AND_SOURCE_CHECKPOINT_REQUIRED')
    diagnostic=check_diagnostic(args.diagnostic,db,src)
    schedule,quota=read_map(args.level_map,args.phase)
    args.quotas=quota
    out=args.output.resolve()
    if out in (db.parent,src.parent) or out/'latest.pt'==src or out/'latest.pt'==db:
        raise ValueError('ISOLATED_OUTPUT_REQUIRED')
    out.mkdir(parents=True,exist_ok=True)
    elapsed_start=time.monotonic()
    model=ex.load_current(src,args.device,args.dtype,args.allow_model_download,args.seed)
    sha={'database':ex.filehash(db),'source':ex.filehash(src)}
    pools=eligible_pools(db,model,quota)
    plan={'schema':SCHEMA,'trainer_version':TRAINER_VERSION,
          'source_checkpoint_sha256':sha['source'],'database_sha256':sha['database'],
          'map_sha256':ex.filehash(args.level_map),
          'seed':args.seed,'lr':args.lr,'op_weight':args.op_weight,
          'cache_per_level':args.cache_per_level,'retention_cache':args.retention_cache,
          'refresh_every':args.refresh_every,
          'diagnostic_sha256':ex.filehash(args.diagnostic),
          'phase_definitions':schedule['phases']}
    config_path=out/'ladder-config.json'
    if config_path.exists():
        if json.loads(config_path.read_text())!=plan:
            raise RuntimeError('LADDER_CONFIGURATION_CHANGED_START_A_NEW_LINEAGE')
    else:ex.write_json(config_path,plan)
    opt=torch.optim.AdamW(list(model['decoder'].parameters())+list(model['gamehead'].parameters()),lr=args.lr)
    legacy=base.registers.build_plan(args.seed)[1]
    reserved={(min(int(e['a']),int(e['b'])),max(int(e['a']),int(e['b']))) for e in legacy if e['split']!='train'}
    stream=base.Stream(args.seed+303,reserved)
    state_path=out/'latest.pt';step=0;history=[]
    if state_path.exists():
        cp=torch.load(state_path,map_location='cpu',weights_only=True)
        if cp.get('schema')!=SCHEMA or cp.get('trainer_version')!=TRAINER_VERSION or \
           cp.get('source_sha256')!=sha['source'] or cp.get('database_sha256')!=sha['database']:
            raise RuntimeError('LADDER_RESUME_IDENTITY_DRIFT')
        model['decoder'].load_state_dict(cp['decoder'],strict=True)
        model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
        opt.load_state_dict(cp['optimizer'])
        torch.set_rng_state(cp['torch_rng_cpu'])
        if args.device=='cuda' and cp.get('torch_rng_cuda') is not None:
            torch.cuda.set_rng_state_all(cp['torch_rng_cuda'])
        step=int(cp['step']);history=list(cp['phase_history'])
    if args.steps<=step:raise ValueError('STEPS_MUST_EXCEED_CURRENT_LADDER_STEP')
    if not history or history[-1]['phase']!=args.phase:
        history.append({'phase':args.phase,'first_step':step+1,'quotas':quota,
                        'map_sha256':plan['map_sha256']})
    args.cache_window=step//args.refresh_every
    # If checkpoint is exactly at a window boundary, next step begins next bank.
    bank=_get_feature_cache(model,pools,quota,stream,args,out,sha,plan['map_sha256'])
    slots=[level for level,n in sorted(quota.items()) for _ in range(n)]
    print(ex.canon({'event':'ladder_map_ready','phase':args.phase,'from_step':step,
                    'target_step':args.steps,'quotas':quota,
                    'cached_cases':{k:len(v) for k,v in bank['levels'].items()},
                    'retention_cached':len(bank['retention']),
                    'diagnostic_source_step':diagnostic['models']['source']['step']}),flush=True)
    def save(t,loss):
        cp={'schema':SCHEMA,'trainer_version':TRAINER_VERSION,
            'step':t,'source_sha256':sha['source'],
            'database_sha256':sha['database'],'phase_history':history,
            'decoder':model['decoder'].state_dict(),
            'game_operator_head':model['gamehead'].state_dict(),
            'optimizer':opt.state_dict(),
            'torch_rng_cpu':torch.get_rng_state(),'last_loss':loss}
        if args.device=='cuda':cp['torch_rng_cuda']=torch.cuda.get_rng_state_all()
        tmp=out/'latest-tmp.pt';torch.save(cp,tmp);os.replace(tmp,state_path)
        print(ex.canon({'event':'ladder_map_checkpoint','step':t,'phase':args.phase,
                        'loss':round(loss,6),'path':str(state_path)}),flush=True)
    last_loss=float('nan');completed=step;reason='target_reached'
    try:
        for t in range(step+1,args.steps+1):
            wanted=(t-1)//args.refresh_every
            if wanted != args.cache_window:
                args.cache_window=wanted
                bank=_get_feature_cache(model,pools,quota,stream,args,out,sha,plan['map_sha256'])
            model['decoder'].train();model['gamehead'].train()
            opt.zero_grad(set_to_none=True)
            order=list(slots);random.Random(args.seed+t).shuffle(order)
            batch=[]
            for slot,level in enumerate(order):
                if level=='retention':
                    item=_bank_select(bank['retention'],t,slot,args.seed)
                    batch.append({'feature':item['view'],'x':item['a'],'y':item['b'],'operator':None})
                else:
                    row=_bank_select(bank['levels'][level],t,slot,args.seed)
                    for feature in row['views']:
                        batch.append({'feature':feature,'x':row['x'],'y':row['y'],
                                      'operator':row['operator']})
            loss=_batch_loss(model,batch,args.op_weight)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(list(model['decoder'].parameters())+
                                           list(model['gamehead'].parameters()),1.)
            opt.step()
            last_loss=float(loss.detach().cpu());completed=t
            if t%args.report_every==0:
                print(ex.canon({'event':'ladder_map_progress','step':t,'target_step':args.steps,
                                'loss':round(last_loss,6),
                                'cache_window':args.cache_window,
                                'elapsed_seconds':round(time.monotonic()-elapsed_start,1)}),flush=True)
            timed_out=bool(args.max_wall_hours and
                           (time.monotonic()-elapsed_start)>=args.max_wall_hours*3600)
            if t%args.save_every==0 or t==args.steps or timed_out:
                save(t,last_loss)
            if timed_out:
                reason='wall_limit_reached';break
    except KeyboardInterrupt:
        if completed>step:
            save(completed,last_loss)
        print(ex.canon({'event':'ladder_map_interrupted','step':completed}),flush=True)
        raise
    check_input_hashes({'database':db,'source':src},sha)
    return {'schema':SCHEMA,'trainer_version':TRAINER_VERSION,
            'step':completed,'target_step':args.steps,'stop_reason':reason,
            'phase':args.phase,'output':str(out),'checkpoint':str(state_path),
            'phase_history':history}

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('diagnose','train'))
    p.add_argument('--database',type=Path,default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
    p.add_argument('--source-checkpoint',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--experimental-checkpoint',type=Path,default=Path.home()/'NanoJev'/'runs'/'game_transform_experiment_v1'/'latest.pt')
    p.add_argument('--report',type=Path,default=Path('runtime/game-transform-experiment/t02-isolation.json'))
    p.add_argument('--diagnostic',type=Path,default=Path('runtime/game-transform-experiment/t02-isolation.json'))
    p.add_argument('--levels',default=','.join(DIAG_LEVELS))
    p.add_argument('--limit-per-split',type=int,default=12)
    p.add_argument('--include-unchanged',action='store_true')
    p.add_argument('--level-map',type=Path,default=Path(__file__).with_name('nanojev_game_transform_ladder_map.json'))
    p.add_argument('--phase',default='t02_focus')
    p.add_argument('--output',type=Path,default=Path.home()/'NanoJev'/'runs'/'game_transform_ladder_map_v2')
    p.add_argument('--steps',type=int,default=20)
    p.add_argument('--save-every',type=int,default=5)
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--op-weight',type=float,default=.1)
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--cache-per-level',type=int,default=64,help='Training-only, fixed unique cases cached per ladder level')
    p.add_argument('--retention-cache',type=int,default=64,help='Fixed training-only native-register cases')
    p.add_argument('--report-every',type=int,default=100,help='Print loss/progress each N optimizer steps')
    p.add_argument('--refresh-every',type=int,default=5000,help='Rotate deterministic training-only feature bank every N steps')
    p.add_argument('--max-wall-hours',type=float,default=0.,help='Stop after N hours and save checkpoint; 0 means no wall cap')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.action=='diagnose':diagnose(args)
    else:print(ex.canon({'event':'ladder_map_train_complete',**train(args)}),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
