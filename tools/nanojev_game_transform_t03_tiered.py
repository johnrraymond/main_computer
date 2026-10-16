#!/usr/bin/env python3
"""Tiered T03 numerical-computation curriculum from the proven T02 checkpoint.

A dedicated fork/resume interface built on the installed frozen-Qwen V2 trainer.
It never trains T08, never changes the existing ladder generator (diagnostic SHA),
and uses mathematical oracles for new questions with disjoint test streams.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
from types import SimpleNamespace

from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_phase_cutover as cutover
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_game_transform_level_probe as probe

SCHEMA = 'nanojev.t03-tiered-oracle.v1'
PHASE = 't03_tiered'
TIERS = tuple(f'T03{x}' for x in 'abcdefghi')
EVAL_TIERS = TIERS + ('T03j',)
# T03 30/41 slots, user-requested 75% approximation; no T08 training.
DEFAULT_QUOTAS = {'retention':1,'T01':1,'T02f':1,
                  'T03a':2,'T03b':5,'T03c':4,'T03d':4,'T03e':4,
                  'T03f':3,'T03g':3,'T03h':3,'T03i':2,
                  'T04':2,'T05':2,'T06':2,'T07':2}
assert sum(DEFAULT_QUOTAS.values()) == 41
assert sum(DEFAULT_QUOTAS[k] for k in TIERS) == 30
DEFAULT_SLOTS=','.join(f'{k}={v}' for k,v in DEFAULT_QUOTAS.items())


def parse_slots(text:str)->dict[str,int]:
    result={}
    allowed=set(TIERS)|{'T01','T02f','T04','T05','T06','T07','retention'}
    for entry in text.split(','):
        if '=' not in entry:raise ValueError('INVALID_TIER_SLOT:'+entry)
        key,val=(x.strip() for x in entry.split('=',1))
        if key not in allowed or key in result or not val.isdecimal() or int(val)<1:
            raise ValueError('INVALID_TIER_SLOT:'+entry)
        result[key]=int(val)
    if result.get('retention',0)<1 or not any(k in result for k in TIERS):
        raise ValueError('T03_TIERS_AND_RETENTION_REQUIRED')
    if sum(result.values())>128:raise ValueError('TOO_MANY_TIER_SLOTS')
    return result


def _rng(seed:int, level:str, split:str, index:int)->random.Random:
    digest=hashlib.sha256(f'{seed}|{level}|{split}|{index}'.encode()).digest()
    return random.Random(int.from_bytes(digest[:8],'big'))


def oracle_case(level:str,seed:int,split:str,index:int)->dict:
    """Generate *new* source problems and verify the answer independently.

    New numeric problems use separate train/dev/test streams. T03j is withheld,
    drawn from the other tier families with disjoint question identifiers.
    """
    if level not in EVAL_TIERS:raise ValueError('UNKNOWN_T03_TIER:'+level)
    if split not in ('train','dev','test'):raise ValueError('INVALID_SYNTHETIC_SPLIT')
    if index<0:raise ValueError('INVALID_CASE_INDEX')
    chosen = ('T03b','T03c','T03d','T03e','T03f','T03g','T03h')[index%7] if level=='T03j' else level
    rng=_rng(seed,level,split,index)
    x=rng.randint(-400,400)
    if chosen in ('T03a','T03b'):
        delta=rng.choice([i for i in range(-30,31) if i])
        y=x+delta
        data={'kind':'add','delta':delta}
    elif chosen=='T03c':
        scale=rng.choice((-5,-4,-3,-2,-1,2,3,4,5))
        y=x*scale
        data={'kind':'scale','scale':scale}
    elif chosen in ('T03d','T03e'):
        scale=rng.choice((-5,-4,-3,-2,2,3,4,5))
        delta=rng.randint(-30,30)
        y=scale*x+delta
        data={'kind':'affine','scale':scale,'delta':delta}
    else:
        rows,cols=2+rng.randrange(2),2+rng.randrange(2)
        mat=[[rng.randint(-90,90) for _ in range(cols)] for _ in range(rows)]
        r,c=rng.randrange(rows),rng.randrange(cols)
        if chosen=='T03f':
            x=mat[0][0];y=mat[r][c]
            data={'kind':'lookup','matrix':mat,'row':r,'col':c}
        elif chosen=='T03g':
            direction=rng.choice(('row','column'))
            if direction=='row':target_r,target_c=(r+1)%rows,c
            else:target_r,target_c=r,(c+1)%cols
            x=mat[r][c];y=mat[target_r][target_c]
            data={'kind':'permutation','matrix':mat,'row':r,'col':c,
                  'target_row':target_r,'target_col':target_c,'direction':direction}
        elif chosen=='T03h':
            scale=rng.choice((-3,-2,2,3));delta=rng.randint(-12,12)
            x=mat[0][0];y=scale*mat[r][c]+delta
            data={'kind':'lookup_affine','matrix':mat,'row':r,'col':c,
                  'scale':scale,'delta':delta}
        else:raise AssertionError(chosen)
    # Partition the numeric X domain: even for train, odd for held-out.
    # This prevents exact task duplication across splits, including lookups.
    if x==y or x%2 != (0 if split=='train' else 1):
        return oracle_case(level,seed,split,index+10000001)
    data['tier']=chosen
    if chosen=='T03a':data['visible_y']=True
    row={'id':hashlib.sha256(ex.canon([SCHEMA,level,seed,split,index,data]).encode()).hexdigest(),
         'x':x,'y':y,'operator':'positive_affine','game':'synthetic_'+chosen,
         'tier':level,'data':data,'split':split}
    # Algebraically independent sanity check of the oracle.
    assert compute_oracle(row)==y
    return row


def compute_oracle(row:dict)->int:
    d=row['data'];kind=d['kind'];x=row['x']
    if kind=='add':return x+d['delta']
    if kind=='scale':return x*d['scale']
    if kind=='affine':return x*d['scale']+d['delta']
    if kind=='lookup':return d['matrix'][d['row']][d['col']]
    if kind=='permutation':return d['matrix'][d['target_row']][d['target_col']]
    if kind=='lookup_affine':return d['matrix'][d['row']][d['col']]*d['scale']+d['delta']
    raise AssertionError(kind)


def tier_prompt(level:str,row:dict,orientation:str,mix=None,base=None)->str:
    if level not in EVAL_TIERS:raise ValueError('UNKNOWN_T03_TIER')
    d=row['data'];x=row['x'];kind=d['kind'];y=row['y']
    if orientation not in ('forward','reordered'):raise ValueError('INVALID_ORIENTATION')
    prefix='Compute the missing YYYYYY exactly. Ignore unrelated numbers.\n'
    if kind=='add':
        rule=f'Transformation: add {d["delta"]} to XXXXXX to obtain YYYYYY.'
    elif kind=='scale':
        rule=f'Transformation: multiply XXXXXX by {d["scale"]} to obtain YYYYYY.'
    elif kind=='affine' and d['tier']=='T03e':
        rule=f'To calculate YYYYYY, multiply XXXXXX by {d["scale"]}, then add {d["delta"]}.'
    elif kind=='affine':
        rule=f'Transformation equation: YYYYYY = {d["scale"]} * XXXXXX + ({d["delta"]}).'
    elif kind in ('lookup','permutation','lookup_affine'):
        matrix=ex.canon(d['matrix'])
        if kind=='lookup':
            rule=(f'Payoff matrix: {matrix}. Read row {d["row"]}, column {d["col"]} '
                  'to obtain YYYYYY. Row and column indices are zero-based.')
        elif kind=='permutation':
            rule=(f'Payoff matrix: {matrix}. The selected source payoff XXXXXX is at '
                  f'row {d["row"]}, column {d["col"]}. Apply one {d["direction"]} '
                  f'rotation: the new value YYYYYY comes from row {d["target_row"]}, '
                  f'column {d["target_col"]}. Use zero-based indices.')
        else:
            rule=(f'Payoff matrix: {matrix}. Read row {d["row"]}, column {d["col"]}; '
                  f'multiply that cell by {d["scale"]} and add {d["delta"]} '
                  'to compute YYYYYY. Use zero-based indices.')
    else:raise AssertionError(kind)
    # T03a is a presentation bridge with BOTH registers visible.
    xs=f'XXXXXX = {x}'
    ys=f'YYYYYY = {y}' if d.get('visible_y') else 'YYYYYY = [compute from transformation]'
    lines=(xs,ys) if orientation=='forward' else (ys,xs)
    text=prefix+rule+'\n'+'\n'.join(lines)+'\noperation: Subtract YYYYYY from XXXXXX.\nAnswer:'
    # No computed Y appears in the prompt outside the intended T03a bridge.
    if not d.get('visible_y') and re.search(r'^YYYYYY\s*=\s*'+re.escape(str(y))+r'\s*$',text,re.MULTILINE):
        raise AssertionError('HIDDEN_Y_LEAK')
    return text


def generated_pool(level:str,seed:int,count:int)->list[dict]:
    if count<1:raise ValueError('INVALID_GENERATED_POOL_SIZE')
    result=[];seen=set();i=0
    while len(result)<count:
        row=oracle_case(level,seed,'train',i);i+=1
        # Deduplicate actual problem content, not just generated case IDs.
        key=(row['x'],ex.canon(row['data']))
        if key not in seen:
            seen.add(key);result.append(row)
        if i>count*50:raise RuntimeError('SYNTHETIC_ORACLE_POOL_EXHAUSTED')
    return result


@contextmanager
def install_tier_adapter(seed:int,pool_size:int):
    """Temporarily extend existing trainer without changing its source/hash."""
    original_levels=ladder.LEVELS
    original_trainable=ladder.TRAINABLE
    original_prompt=ladder.contextual_prompt
    original_pools=ladder.eligible_pools
    def prompt(level,row,ori,mix,base):
        if level in TIERS:
            if level=='T03i':return original_prompt('T03',row,ori,mix,base)
            return tier_prompt(level,row,ori,mix,base)
        return original_prompt(level,row,ori,mix,base)
    def pools(db,model,quota):
        old_quota={k:v for k,v in quota.items() if k in original_trainable}
        if 'T03i' in quota:old_quota['T03']=quota['T03i']
        result=original_pools(db,model,old_quota)
        if 'T03i' in quota:
            result['T03i']=result.pop('T03')
        for name in TIERS:
            if name in quota and name!='T03i':
                candidates=generated_pool(name,seed,pool_size)
                # All presentation sizes are checked before spending capture GPU time.
                valid=[]
                for row in candidates:
                    prompts=[tier_prompt(name,row,o) for o in ('forward','reordered')]
                    if all(probe.token_check(model['tokenizer'],text,
                            model['config']['max_prompt_tokens'])['status']=='ok' for text in prompts):
                        valid.append(row)
                if not valid:raise RuntimeError('NO_VALID_GENERATED_TIER:'+name)
                result[name]=valid
        return result
    try:
        ladder.LEVELS=original_levels+TIERS
        ladder.TRAINABLE=original_trainable+TIERS
        ladder.contextual_prompt=prompt
        ladder.eligible_pools=pools
        yield
    finally:
        ladder.LEVELS=original_levels
        ladder.TRAINABLE=original_trainable
        ladder.contextual_prompt=original_prompt
        ladder.eligible_pools=original_pools


def _create_or_resume(args):
    args.from_output=args.from_output.resolve();args.output=args.output.resolve()
    args.database=args.database.resolve();args.source_checkpoint=args.source_checkpoint.resolve()
    args.diagnostic=args.diagnostic.resolve()
    slots=args.slots
    quotas=parse_slots(slots)
    prepared=SimpleNamespace(from_output=args.from_output,output=args.output,
        database=args.database,source_checkpoint=args.source_checkpoint,
        diagnostic=args.diagnostic,slots=slots,steps=args.steps)
    old_phase=cutover.PHASE;old_parser=cutover.parse_slots
    old_expected=cutover._expected_spec
    def validate_slots(text):
        if text!=slots:raise ValueError('TIERED_QUOTA_DRIFT')
        return dict(quotas)
    def expected(parent,quotas):
        doc=old_expected(parent,quotas)
        doc['t03_tier_pool_size']=args.pool_per_tier
        doc['t03_tier_oracle_sha256']=ex.filehash(Path(__file__))
        doc['t03_tier_oracle_schema']=SCHEMA
        return doc
    try:
        cutover.PHASE=PHASE
        cutover.parse_slots=validate_slots
        cutover._expected_spec=expected
        config,created=cutover.prepare(prepared)
    finally:
        cutover.PHASE=old_phase
        cutover.parse_slots=old_parser
        cutover._expected_spec=old_expected
    spec=json.loads((args.output/'level-map.json').read_text())
    if spec.get('t03_tier_oracle_sha256')!=ex.filehash(Path(__file__)) or \
       spec.get('t03_tier_pool_size')!=args.pool_per_tier:
        raise RuntimeError('T03_TIER_GENERATOR_OR_POOL_CHANGED_USE_NEW_LINEAGE')
    # The new lineage can choose a faster cache rotation than its T02 parent.
    # Seal these settings once; never silently change them on resume.
    config_path=args.output/'ladder-config.json'
    if created:
        config['cache_per_level']=args.cache_per_level
        config['refresh_every']=args.refresh_every
        ex.write_json(config_path,config)
    elif (config.get('cache_per_level')!=args.cache_per_level or
          config.get('refresh_every')!=args.refresh_every):
        raise RuntimeError('T03_TIER_CACHE_CONFIG_CHANGED')
    if args.seed!=config['seed']:
        raise RuntimeError('T03_TIER_SEED_MUST_MATCH_PARENT')
    if config['op_weight']!=0:
        raise RuntimeError('T03_TIERS_REQUIRE_ZERO_OPERATOR_LOSS')
    return config,created


def train(args)->dict:
    if args.pool_per_tier<64:raise ValueError('POOL_PER_TIER_MIN_64')
    if args.output.resolve()==args.from_output.resolve():
        raise ValueError('T03_TIERS_MUST_NOT_OVERWRITE_PARENT')
    quotas=parse_slots(args.slots)
    with install_tier_adapter(args.seed,args.pool_per_tier):
        config,created=_create_or_resume(args)
        argv=['train','--database',str(args.database),'--source-checkpoint',str(args.source_checkpoint),
              '--diagnostic',str(args.diagnostic),'--level-map',str(args.output/'level-map.json'),
              '--phase',PHASE,'--output',str(args.output),'--steps',str(args.steps),
              '--save-every',str(args.save_every),'--report-every',str(args.report_every),
              '--max-wall-hours',str(args.max_wall_hours),
              '--seed',str(config['seed']),'--lr',str(config['lr']),
              '--op-weight',str(config['op_weight']),
              '--cache-per-level',str(args.cache_per_level),
              '--retention-cache',str(config['retention_cache']),
              '--refresh-every',str(args.refresh_every),
              '--device',args.device,'--dtype',args.dtype]
        if args.allow_model_download:argv.append('--allow-model-download')
        # Trainer's own strict config check catches changed settings on resume.
        train_args=ladder.parser().parse_args(argv)
        print(ex.canon({'event':'t03_tiered_cutover_ready','created':created,
                        'parent':str(args.from_output),'output':str(args.output),
                        'phase':PHASE,'quotas':quotas,'parent_step':'see provenance',
                        'generator_sha256':ex.filehash(Path(__file__))}),flush=True)
        outcome=ladder.train(train_args)
        print(ex.canon({'event':'t03_tiered_train_complete',**outcome}),flush=True)
        return outcome


def evaluate(args)->dict:
    import torch
    db=args.database.resolve();src=args.source_checkpoint.resolve()
    paths=[db,src,args.experimental_checkpoint.resolve(),args.from_output/'latest.pt']
    if not all(p.is_file() for p in paths):raise FileNotFoundError('EVALUATION_CHECKPOINT_MISSING')
    hashes=[ex.filehash(p) for p in paths]
    rows={}
    # Synthetic heldouts are disjoint by generator split, never cached for training.
    for split in ('dev','test'):
        for level in EVAL_TIERS:
            if level=='T03i':continue
            rows[(split,level)]=[oracle_case(level,args.seed,split,i) for i in range(args.limit)]
    with closing(sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True)) as cn:
        for split in ('dev','test','game_transfer'):
            items=ladder.materialize_source(cn,probe.select_balanced(ex.rows_for(cn,split),args.limit))
            rows[(split,'T03i')]=[r for r in items if probe.available_integer(r) and r['x']!=r['y']]
    report={'schema':SCHEMA,'generator_sha256':ex.filehash(Path(__file__)),
            'source_checkpoint_sha256':hashes[1],
            'database_sha256':hashes[0],
            'checkpoint_sha256':hashes[2],
            'baseline_checkpoint_sha256':hashes[3],
            'scores':{},'steps':{}}
    for label,cp_path in [('baseline',paths[3]),('trained',paths[2])]:
        model=ex.load_current(src,args.device,args.dtype,args.allow_model_download,args.seed)
        cp=torch.load(cp_path,map_location='cpu',weights_only=True)
        if cp['source_sha256']!=hashes[1] or cp['database_sha256']!=hashes[0]:
            raise RuntimeError('EVAL_LINEAGE_DRIFT')
        model['decoder'].load_state_dict(cp['decoder'],strict=True)
        model['gamehead'].load_state_dict(cp['game_operator_head'],strict=True)
        report['steps'][label]=int(cp['step'])
        model['decoder'].eval();model['gamehead'].eval()
        for (split,level),items in rows.items():
            records=[]
            for item in items:
                for orientation in ('forward','reordered'):
                    prompt=(probe.render('T03',item,orientation,model['mix'],model['base'])
                            if level=='T03i' else tier_prompt(level,item,orientation))
                    validity=probe.token_check(model['tokenizer'],prompt,model['config']['max_prompt_tokens'])
                    rec={'case_id':item['id'],'game':item['game'],'operator':item['operator'],
                         'split':split,'level':level,'orientation':orientation,
                         'truth':{'x':item['x'],'y':item['y']},'status':validity['status']}
                    if validity['status']=='ok':
                        rec['prediction'],rec['predicted_operator']=ex.predict_one(model,prompt)
                    records.append(rec)
            scores={}
            for threshold in (.05,.10):
                m=probe.score(records,level,threshold)
                scores[str(threshold)]={'n':m['n'],'paired_xy':m['paired']['joint'],
                     'x':m['paired']['x'],'y':m['paired']['y'],'unscorable':m['unscorable']}
            report['scores'][f'{label}/{split}/{level}']=scores
        del model
        if args.device=='cuda':torch.cuda.empty_cache()
    if any(ex.filehash(p)!=h for p,h in zip(paths,hashes)):
        raise RuntimeError('READ_ONLY_EVALUATION_INPUT_CHANGED')
    if args.report:ex.write_json(args.report,report)
    print(ex.canon({'event':'t03_tiers_probe_complete','steps':report['steps'],
         'report':str(args.report),'scores':report['scores']}),flush=True)
    return report


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('train','probe'))
    p.add_argument('--database',type=Path,default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
    p.add_argument('--source-checkpoint',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--diagnostic',type=Path,default=Path('runtime/game-transform-experiment/t02-isolation-v2.json'))
    p.add_argument('--from-output',type=Path,default=Path.home()/'NanoJev/runs/game_transform_ladder_t02_corrected')
    p.add_argument('--output',type=Path,default=Path.home()/'NanoJev/runs/game_transform_t03_tiered')
    p.add_argument('--experimental-checkpoint',type=Path,default=Path.home()/'NanoJev/runs/game_transform_t03_tiered/latest.pt')
    p.add_argument('--steps',type=int,default=240000,help='Cumulative target; T02 fork begins at 220000')
    p.add_argument('--save-every',type=int,default=500)
    p.add_argument('--report-every',type=int,default=100)
    p.add_argument('--max-wall-hours',type=float,default=8)
    p.add_argument('--cache-per-level',type=int,default=64)
    p.add_argument('--refresh-every',type=int,default=1000)
    p.add_argument('--pool-per-tier',type=int,default=2048)
    p.add_argument('--slots',default=DEFAULT_SLOTS,help='Exact tier weights, comma-separated; T08 prohibited')
    p.add_argument('--limit',type=int,default=48)
    p.add_argument('--report',type=Path,default=Path('runtime/game-transform-experiment/t03-tiered-probe.json'))
    p.add_argument('--seed',type=int,default=20261015)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None)->int:
    args=parser().parse_args(argv)
    if args.action=='train':train(args)
    else:evaluate(args)
    return 0

if __name__=='__main__':raise SystemExit(main())
