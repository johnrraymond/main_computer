#!/usr/bin/env python3
"""Read-only error concentration map around Qwen's acquired mathematical frontier.

Paired backbone / optional glass replay. Two tasks have actual operand-radius
scaling; seven others use their *native* generator distributions/submodes,
explicitly not a fictitious numeric radius. All cases are new deterministic
indices, plus optional replay of previously discovered failures. Nothing here
modifies a model checkpoint, optimizer, training state, or glass.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import csv
import re
import os
from pathlib import Path
import secrets
import statistics
from types import SimpleNamespace

from tools import nanojev_qwen_lm_frontier_tasks as tasks

SCHEMA='nanojev.qwen.frontier.error-map.v1'
BANDS=(('edge',1.0),('inner_1',0.9375),('outer_1',1.0625),
       ('inner_2',0.875),('outer_2',1.125))
MAGNITUDE_TASKS=frozenset(('signed_arithmetic','arithmetic_operators'))
MODULI={'place_value':3,'fractions_units':4,'algebra':3,
        'logic_graphs':5,'coordinates_geometry':3,'probability':3,
        'symbolic_scale':12,'signed_arithmetic':4,'arithmetic_operators':5}


def _digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _atomic_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf8') as fp:
        json.dump(obj,fp,sort_keys=True,indent=2,ensure_ascii=False)
        fp.write('\n');fp.flush();os.fsync(fp.fileno())
    os.replace(tmp,path)


def _append(path,obj):
    with Path(path).open('a',encoding='utf8') as fp:
        fp.write(json.dumps(obj,sort_keys=True,ensure_ascii=False)+'\n')
        fp.flush();os.fsync(fp.fileno())


def _emit(tag,body):
    print(tag+'='+json.dumps(body,sort_keys=True,ensure_ascii=False),flush=True)


def _path_inside(child,parent):
    child=Path(child).resolve();parent=Path(parent).resolve()
    return child==parent or parent in child.parents


def _safe_output(root,protected):
    for source in protected:
        if _path_inside(root,source) or _path_inside(source,root):
            raise RuntimeError('ERROR_MAP_REPORT_OVERLAPS_INPUT:'+str(source))


def _read_jsonl(path,*,repair_tail=False):
    """Recover complete JSON lines; optionally truncate torn own-report tail."""
    if not Path(path).exists():return []
    with Path(path).open('rb') as fp:lines=fp.readlines()
    out=[]
    for i,line in enumerate(lines):
        try:out.append(json.loads(line))
        except (ValueError,UnicodeDecodeError):
            if i!=len(lines)-1:raise RuntimeError('ERROR_MAP_DAMAGED_HISTORY:'+str(path))
            if repair_tail:
                with Path(path).open('r+b') as fp:fp.truncate(sum(len(x) for x in lines[:i]))
    return out


def _native_mode(task,index):
    return index%MODULI[task]


def _case_deck(policy):
    """Edge-first, bidirectional magnitude bands where generator supports it.

    Each task/level uses an independent seeded stream. Native-only tasks
    explicitly have no meaningful 1024-radius coordinate in their generator.
    Index spacing (97) cycles modulo 3, 4 and 5, preserving submodes.
    """
    rows=[];seen_ids=set();seen_inputs=set()
    for task,level in tasks.task_order(policy['levels']):
        bands=BANDS if task in MAGNITUDE_TASKS else (('native',1.),)
        for band_idx,(band,scale) in enumerate(bands):
            boundary=round(policy['operand_boundary']*scale)
            for i in range(policy['cases_per_band']):
                idx=410_000_000+band_idx*1_000_000+level*100_000+i*97+17
                row=tasks.make_case(task,level,idx,seed=policy['seed']+band_idx*1009,
                    operand_boundary=boundary if task in MAGNITUDE_TASKS
                                             else policy['operand_boundary'],
                    start_rung=policy['rung'])
                # Finite logical templates may yield duplicate text despite
                # distinct IDs. Never inflate evidence by counting duplicates.
                key=(row['source'],row['target'])
                if key in seen_inputs:continue
                if row['id'] in seen_ids:raise RuntimeError('ERROR_MAP_DUPLICATE_CASE_ID')
                seen_inputs.add(key);seen_ids.add(row['id'])
                magnitude=policy['operand_boundary']*(2**level)
                measured=row.get('metadata',{}).get('operand_magnitude')
                if task=='arithmetic_operators':
                    match=re.match(r'PROBLEM: ([0-9]+)',row['source'])
                    measured=int(match.group(1)) if match else None
                rows.append({**row,'origin':'fresh','band':band,
                    'mode':_native_mode(task,idx),
                    'radius_axis':'operand_magnitude' if task in MAGNITUDE_TASKS else 'native_task_scale',
                    'target_operand_boundary':round(magnitude*scale) if task in MAGNITUDE_TASKS else None,
                    'configured_operand_boundary':magnitude if task in MAGNITUDE_TASKS else None,
                    'measured_operand_magnitude':measured,
                    'location_ratio':scale if task in MAGNITUDE_TASKS else None})
    return rows,seen_inputs


def _historical_rows(policy,systematic,samples,known_inputs):
    if not policy['include_history']:return []
    out=[];seen=set(known_inputs)
    for phase,path in [('systematic',Path(systematic)/'discovered_errors.jsonl'),
                       ('samples',Path(samples)/'mixed_discovered_errors.jsonl')]:
        for entry in _read_jsonl(path):
            task=entry.get('task');level=entry.get('level')
            source=entry.get('source');target=entry.get('target')
            if task not in tasks.TASKS or not isinstance(level,int) or not 0<=level<policy['levels']:
                continue
            if not isinstance(source,str) or not isinstance(target,str) or not source or not target:continue
            key=(source,target)
            if key in seen:continue
            seen.add(key)
            digest=hashlib.sha256((source+'\0'+target).encode()).hexdigest()[:20]
            out.append({'id':f'history:{digest}','task':task,'level':level,
                'source':source,'target':target,'metadata':{},'terminal_kind':'historical',
                'origin':'historical','historical_phase':phase,
                'historical_discovery_step':entry.get('step'),
                'band':'historical','mode':None,'radius_axis':'historical_unmeasured',
                'configured_operand_boundary':None,'target_operand_boundary':None,
                'measured_operand_magnitude':None,'location_ratio':None})
    return out


def _row_summary(rows,branch='baseline'):
    count=len(rows); correct=sum(bool(r[branch]['correct']) for r in rows)
    losses=[r['baseline']['teacher_forced_loss'] for r in rows]
    result={'tested':count,'correct':correct,'wrong':count-correct,
        'accuracy':correct/count if count else None,
        'mean_loss':statistics.fmean(losses) if losses else None,
        'median_loss':statistics.median(losses) if losses else None,
        'max_loss':max(losses) if losses else None}
    if branch!='baseline':
        result['fixed']=sum(not r['baseline']['correct'] and r[branch]['correct'] for r in rows)
        result['regressed']=sum(r['baseline']['correct'] and not r[branch]['correct'] for r in rows)
        result['net_correct_gain']=result['fixed']-result['regressed']
    return result


def _aggregate(results,branch='baseline'):
    groups={'all':results}
    for r in results:
        for label in (f"{r['task']}:L{r['level']}",
                      f"{r['task']}:L{r['level']}:{r['band']}",
                      f"{r['task']}:L{r['level']}:mode{r['mode']}",
                      f"origin:{r['origin']}"):
            groups.setdefault(label,[]).append(r)
    return {name:_row_summary(group,branch) for name,group in sorted(groups.items())}


def _hotspots(results):
    groups=defaultdict(list)
    for r in results:
        if r['origin']!='fresh':continue
        groups[(r['task'],r['level'],r['band'])].append(r)
    return sorted(({'task':task,'level':level,'band':band,**_row_summary(group)}
                   for (task,level,band),group in groups.items()),
                  key=lambda r:(-r['wrong'],-r['mean_loss'],r['task'],r['level']))


def _frontier_rolloff(results,branches):
    """Return observed failures and regressions by ascending operand band.

    A single first failure is not claimed as a monotonic competence boundary.
    The map retains every band, including any recovered outer band.
    """
    output={}
    for task in sorted(MAGNITUDE_TASKS):
        for level in sorted({r['level'] for r in results if r['task']==task}):
            rows=[r for r in results if r['task']==task and r['level']==level and
                  r['origin']=='fresh']
            bands=[]
            for band,scale in sorted(BANDS,key=lambda t:t[1]):
                subset=[r for r in rows if r['band']==band]
                row={'band':band,'radius_ratio':scale,
                     'target_operand_magnitude':round(subset[0]['configured_operand_boundary']*scale)
                         if subset else None,
                     **_row_summary(subset)}
                for branch in branches:
                    metric=_row_summary(subset,branch)
                    row[branch+'_fixed']=metric['fixed']
                    row[branch+'_regressed']=metric['regressed']
                bands.append(row)
            failing=[r['band'] for r in bands if r['wrong']>0]
            output[f'{task}:L{level}']={'bands':bands,
                'first_observed_failure_band':failing[0] if failing else None,
                'note':'Sampled first failure, not an exact or monotonic competence threshold.'}
    return output


def _csv_heatmap(root,summary):
    file=root/'frontier_heatmap.csv'
    with file.open('w',encoding='utf8',newline='') as fp:
        writer=csv.DictWriter(fp,fieldnames=['task','level','band','tested','correct','wrong',
            'accuracy','mean_loss','median_loss','max_loss'])
        writer.writeheader()
        for item in sorted(summary['hotspots'],key=lambda r:(r['task'],r['level'],r['band'])):
            writer.writerow({key:item.get(key) for key in writer.fieldnames})
    return file.name

def _candidate(row):
    # Fully specified exact target packet for a future explicit glass adapter.
    return {k:row[k] for k in ('id','task','level','source','target','origin','band',
                                'mode','radius_axis','target_operand_boundary','location_ratio')}


def _exports(root,results,fragile_loss):
    destinations={'hard':'hard_candidates.jsonl','fragile':'fragile_candidates.jsonl',
                  'replay':'retention_replay.jsonl'}
    counts={k:0 for k in destinations}
    temp={kind:(root/(name+'.tmp')).open('w',encoding='utf8') for kind,name in destinations.items()}
    try:
        for row in results:
            base=row['baseline'];kind=('hard' if not base['correct'] else
                  'fragile' if base['teacher_forced_loss']>=fragile_loss else 'replay')
            temp[kind].write(json.dumps({**_candidate(row),
                 'baseline_loss':base['teacher_forced_loss'],
                 'baseline_correct':base['correct']},sort_keys=True)+'\n')
            counts[kind]+=1
    finally:
        for fp in temp.values():fp.close()
    for kind,name in destinations.items():os.replace(root/(name+'.tmp'),root/name)
    return {'counts':counts,'files':destinations,'fragile_loss_threshold':fragile_loss}


def _verify_inputs(args):
    from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
    from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
    samples=Path(args.samples_output).resolve()
    systematic=Path(args.systematic_output).resolve()
    source=samples/'glass_ready.pt'
    if not source.is_file():raise FileNotFoundError('ERROR_MAP_REQUIRES_GLASS_READY:'+str(source))
    ck=glass._verify_backbone(source)
    state=ck['state']
    if state.get('status') not in ('PLATEAU','COMPLETE'):
        # Verify checkpoint identity, not mutable training state status.
        if state.get('best_step')!=state.get('step'):raise RuntimeError('ERROR_MAP_BACKBONE_NOT_BEST')
    if args.levels is not None and args.levels!=state['policy']['source_levels']:
        raise ValueError('ERROR_MAP_SOURCE_LEVELS_DRIFT')
    if args.operand_boundary is not None and args.operand_boundary!=state['policy']['operand_boundary']:
        raise ValueError('ERROR_MAP_OPERAND_BOUNDARY_DRIFT')
    for path in (args.stage1_checkpoint,args.text_checkpoint):
        if not Path(path).is_file():raise FileNotFoundError(path)
    root=Path(args.report_root).resolve() if args.report_root else samples.parent/(samples.name+'_error_maps')
    _safe_output(root,[samples,systematic,Path(args.stage1_checkpoint).resolve().parent,
                       Path(args.text_checkpoint).resolve().parent])
    source_hash=fr._sha256(source)
    stage=Path(args.glasses_output).resolve() if args.glasses_output else samples.with_name(
        (samples.name[:-3] if samples.name.endswith('_v1') else samples.name)+'_glasses_v1')
    glass_inputs={}
    if args.glass_stage:
        for name in (('A','B') if args.branches=='both' else (args.branches,)):
            file=stage/args.glass_stage/f'best_{name}.pt'
            if not file.is_file():raise FileNotFoundError('ERROR_MAP_GLASS_BEST_MISSING:'+str(file))
            glass_inputs[name]=file
    _safe_output(root,[stage] if glass_inputs else [])
    return source,ck,root,source_hash,glass_inputs


def _glass_copies(args,glass_inputs,root,source_hash):
    import torch
    from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
    from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
    copies={}
    for name,file in glass_inputs.items():
        dest=root/('best_'+name+'_snapshot.pt')
        if not dest.exists():fr._atomic_copy(file,dest)
        obj=torch.load(dest,map_location='cpu',weights_only=True)
        if obj.get('schema')!=glass.SCHEMA or obj.get('name')!=name or not isinstance(obj.get('weights'),dict):
            raise RuntimeError('ERROR_MAP_INCOMPATIBLE_GLASS:'+name)
        policy=obj.get('policy') or {}
        if policy.get('backbone_sha256')!=source_hash:
            raise RuntimeError('ERROR_MAP_GLASS_BACKBONE_LINEAGE_DRIFT:'+name)
        copies[name]=(obj,fr._sha256(dest))
    return copies


def _evaluate(bb,tok,row,args,glasses,features):
    import torch
    from tools import nanojev_qwen_lm_radius_ladder as ladder
    from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    with torch.no_grad():
        loss=ladder._batch_loss(bb,tok,[row],args.device,args.max_seq)
        if not bool(torch.isfinite(loss)):raise RuntimeError('ERROR_MAP_NONFINITE_LOSS:'+row['id'])
        loss=float(loss.detach())
    if glasses:
        basic=ab._rollout(bb,tok,row,features)
        output=basic['output'];correct=bool(basic['correct'])
    else:
        output=fr._greedy_one(bb,tok,row,args)
        correct=bool(ladder.correctness(row['source'],row['target'],output)[0])
    result={'baseline':{'correct':correct,'output':output,
                        'teacher_forced_loss':loss}}
    for name,g in glasses.items():
        a=ab._rollout(bb,tok,row,features,glass=g)
        result[name]={'correct':bool(a['correct']),'output':a['output'],
                      'transition':('retained' if correct and a['correct'] else
                                    'regressed' if correct else
                                    'fixed' if a['correct'] else 'still_wrong'),
                      'mean_gate_probability':a['mean_gate_probability'],
                      'mean_delta_norm':a['mean_delta_norm']}
    return result


def _create_plan(args,source_hash,ck):
    state=ck['state'];p=state['policy']
    return {'schema':SCHEMA,'source_hash':source_hash,'source_step':state['step'],
        'seed':args.seed,'cases_per_band':args.cases_per_band,
        'include_history':not args.no_history,'levels':p['source_levels'],
        'operand_boundary':p['operand_boundary'],
        'rung':p['start_rung_override'] if p['start_rung_override']>=0
               else max(0,int(state['seed_state']['rung'])),
        'stage1_checkpoint':str(Path(args.stage1_checkpoint).resolve()),
        'text_checkpoint':str(Path(args.text_checkpoint).resolve()),
        'max_seq':args.max_seq,'max_tokens':args.max_tokens,
        'max_new_tokens':args.max_new_tokens,'fragile_loss':args.fragile_loss,
        'glass_stage':args.glass_stage,'branches':args.branches if args.glass_stage else None}


def _model_args(args):
    return SimpleNamespace(device=args.device,max_tokens=args.max_tokens,
        max_new_tokens=args.max_new_tokens,max_seq=args.max_seq,
        feature_layers=None,decode_cache=True,decode_progress_tokens=16)


def run(args):
    from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
    from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    source,ck,root,source_hash,inputs=_verify_inputs(args)
    policy=_create_plan(args,source_hash,ck)
    if args.resume_report:
        report=Path(args.resume_report).resolve()
        if report.parent!=root:raise RuntimeError('ERROR_MAP_RESUME_OUTSIDE_REPORT_ROOT')
        record=json.loads((report/'manifest.json').read_text(encoding='utf8'))
        if record['policy']!=policy:raise RuntimeError('ERROR_MAP_RESUME_POLICY_DRIFT')
    else:
        root.mkdir(parents=True,exist_ok=True)
        report=root/(datetime.now(timezone.utc).strftime('map_%Y%m%dT%H%M%S_%fZ_')+
                     secrets.token_hex(3))
        report.mkdir(exist_ok=False)
        _atomic_json(report/'manifest.json',{'schema':SCHEMA,'policy':policy,'status':'RUNNING'})
    # Index IDs are deterministic. No regenerated or previously trained samples.
    rows,known_inputs=_case_deck(policy)
    rows+=_historical_rows(policy,args.systematic_output,args.samples_output,known_inputs)
    # Verify guard before loading the expensive Qwen backbone.
    checkpoint_copies=_glass_copies(args,inputs,report,source_hash)
    manifest=json.loads((report/'manifest.json').read_text(encoding='utf8'))
    hashes={name:record[1] for name,record in checkpoint_copies.items()}
    if manifest.get('glass_hashes') not in (None,hashes):
        raise RuntimeError('ERROR_MAP_RESUME_GLASS_CHANGED')
    manifest['glass_hashes']=hashes
    manifest['total_cases']=len(rows)
    _atomic_json(report/'manifest.json',manifest)
    existing=_read_jsonl(report/'results.jsonl',repair_tail=True);evaluated={}
    for item in existing:
        if item['id'] in evaluated:raise RuntimeError('ERROR_MAP_DUPLICATE_RESULTS')
        evaluated[item['id']]=item
    expected={row['id'] for row in rows}
    if any(key not in expected for key in evaluated):raise RuntimeError('ERROR_MAP_FOREIGN_RESULT')
    _emit('QWEN_FRONTIER_ERROR_MAP_START',{'report':str(report),'source_step':policy['source_step'],
          'cases':len(rows),'reused':len(evaluated),'glass_branches':list(checkpoint_copies),
          'radius_bands':[x[0] for x in BANDS],'radius_scaled_tasks':sorted(MAGNITUDE_TASKS)})
    if len(evaluated)<len(rows):
        import torch
        parent_args=SimpleNamespace(stage1_checkpoint=args.stage1_checkpoint,
            text_checkpoint=args.text_checkpoint,device=args.device,
            baseline=None,backbone=None,activation=None,format=None,
            allow_model_download=False)
        bb,tok,_=glass._load_backbone(parent_args,source,ck['state'])
        bb.eval();bb.requires_grad_(False)
        hidden=int(bb.get_output_embeddings().weight.shape[1]);gs={}
        for name,(obj,sha) in checkpoint_copies.items():
            gp=obj['policy']
            g=ab.ContinuousGlass(hidden,gp['feature_layers'],gp['width'],name=='B')
            g.net.to(args.device);g.net.eval();g.net.load_state_dict(obj['weights'])
            gs[name]=g
        if gs:
            values={checkpoint_copies[name][0]['policy']['feature_layers'] for name in gs}
            if len(values)!=1:raise RuntimeError('ERROR_MAP_GLASS_FEATURE_LAYER_MISMATCH')
        features=_model_args(args)
        features.feature_layers=next(iter(values)) if gs else 4
        for i,row in enumerate(rows,1):
            if row['id'] in evaluated:continue
            response=_evaluate(bb,tok,row,args,gs,features)
            item={**row,**response}
            _append(report/'results.jsonl',item)
            evaluated[row['id']]=item
            if i%args.progress_every==0 or i==len(rows):
                _emit('QWEN_FRONTIER_ERROR_MAP_PROGRESS',{'tested':len(evaluated),
                     'total':len(rows),'task':row['task'],'level':row['level'],
                     'band':row['band'],'last_correct':item['baseline']['correct'],
                     'last_loss':round(item['baseline']['teacher_forced_loss'],6)})
    if fr._sha256(source)!=source_hash:raise RuntimeError('ERROR_MAP_SOURCE_CHANGED_DURING_AUDIT')
    results=[evaluated[r['id']] for r in rows]
    summary={'schema':SCHEMA,'status':'COMPLETE','report_dir':str(report),
        'source_step':policy['source_step'],'source_sha256':source_hash,
        'case_count':len(results),'baseline':_aggregate(results),
        'hotspots':_hotspots(results),
        'radius_rolloff':_frontier_rolloff(results,checkpoint_copies),
        'scope':{'radius_scaled_tasks':sorted(MAGNITUDE_TASKS),
                 'native_scale_tasks':sorted(set(tasks.TASKS)-MAGNITUDE_TASKS),
                 'notes':'Only signed arithmetic and arithmetic operators respond to operand_boundary. Other tasks are probed using native exact-generator cases/submodes; L0/L1 is not a common numeric magnitude scale.'},
        'grade':'Exact greedy next-PROBLEM packet; reference loss is teacher-forced per-case cross entropy, not failure count.',
        'certification':'DIAGNOSTIC_ONLY; finite generated sample, not a mastery certificate'}
    for name in checkpoint_copies:
        summary[name]=_aggregate(results,name)
    summary['exports']=_exports(report,results,policy['fragile_loss'])
    summary['heatmap_csv']=_csv_heatmap(report,summary)
    _atomic_json(report/'summary.json',summary)
    manifest['status']='COMPLETE';_atomic_json(report/'manifest.json',manifest)
    _emit('QWEN_FRONTIER_ERROR_MAP_DONE',{'report':str(report),
        'baseline':summary['baseline']['all'],
        'hotspots':summary['hotspots'][:8],
        'exports':summary['exports']['counts'],
        'glass_comparison':{name:summary[name]['all'] for name in checkpoint_copies}})
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--samples-output',required=True,help='Completed Phase 2 directory containing glass_ready.pt')
    p.add_argument('--systematic-output',required=True,help='Phase 1 directory for recorded failure replay')
    p.add_argument('--stage1-checkpoint',required=True)
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--report-root',help='Separate read-only-audit output; defaults beside samples directory')
    p.add_argument('--resume-report',help='Resume an interrupted report under --report-root')
    p.add_argument('--glasses-output',help='Glass workspace; defaults beside samples directory')
    p.add_argument('--glass-stage',choices=('easy','mixed','hard'),help='Optional paired test of selected best A and B glasses')
    p.add_argument('--branches',choices=('A','B','both'),default='both')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--seed',type=int,default=48157)
    p.add_argument('--cases-per-band',type=int,default=12)
    p.add_argument('--no-history',action='store_true',help='Do not recheck Phase 1 and Phase 2 logged failures')
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--max-tokens',type=int,default=256)
    p.add_argument('--max-new-tokens',type=int,default=64)
    p.add_argument('--fragile-loss',type=float,default=0.1,help='Loss threshold to flag correct-but-fragile cases')
    p.add_argument('--progress-every',type=int,default=12)
    p.add_argument('--levels',type=int,help='Optional assertion against saved source levels')
    p.add_argument('--operand-boundary',type=int,help='Optional assertion against saved source operand boundary')
    args=p.parse_args(argv)
    if (args.cases_per_band<1 or args.seed<0 or args.fragile_loss<0 or
        args.max_new_tokens<1 or args.max_seq<16 or args.max_tokens<args.max_seq or
        args.progress_every<1):p.error('invalid diagnostic limits')
    return run(args)


if __name__=='__main__':main()
