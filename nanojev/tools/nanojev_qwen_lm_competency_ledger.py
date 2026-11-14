#!/usr/bin/env python3
"""Paired net-competency observations made DURING Qwen frontier training.

Never confuses fresh successes with acquired gains. An answer counts as gained
or lost only when the EXACT SAME source/target was scored in a prior check.
Hard examples are durably retained as prompt/target cases for later glasses.
This does not alter a backbone, optimizer, training RNG, or training policy.
"""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr

SCHEMA='nanojev.qwen.frontier.competency-ledger.v1'
BANDS=(('edge',1.0),('inner_1',.9375),('outer_1',1.0625),('inner_2',.875),('outer_2',1.125))
RADIAL=frozenset(('signed_arithmetic','arithmetic_operators'))


def _key(row):
    return hashlib.sha256((row['source']+'\0'+row['target']).encode('utf8')).hexdigest()


def _read(path):
    if not Path(path).is_file():return []
    result=[]
    data=Path(path).read_bytes().splitlines(keepends=True)
    for i,line in enumerate(data):
        if not line.strip():continue
        try:result.append(json.loads(line))
        except (ValueError,UnicodeDecodeError) as exc:
            if i==len(data)-1 and not line.endswith(b'\n'):break
            raise RuntimeError('COMPETENCY_LEDGER_CORRUPT:'+str(path)) from exc
    return result


def _append_once(path,obj,identity):
    path=Path(path)
    existing=_read(path)
    if identity(obj) in {identity(item) for item in existing}:return False
    path.parent.mkdir(parents=True,exist_ok=True)
    # An interrupted partial last line must be repaired before appending.
    if path.exists():
        data=path.read_bytes()
        if data and not data.endswith(b'\n'):
            last=data.rfind(b'\n')
            with path.open('r+b') as f:f.truncate(last+1)
    fr._append(path,obj)
    return True


def _make(state,task,level,index,seed,band):
    policy=state['policy']
    operand=policy['operand_boundary']
    factor=dict(BANDS).get(band,1.0)
    radius=round(operand*factor) if task in RADIAL else operand
    rung=(policy['start_rung_override'] if policy['start_rung_override']>=0 else
          max(0,int(state['seed_state']['rung'])))
    item=tasks.make_case(task,level,index,seed=seed,
                         operand_boundary=radius,start_rung=rung)
    return {**item,'band':band,'probe_seed':seed,'probe_index':index,
            'probe_operand_boundary':radius,
            'nominal_operand_anchor':operand*(2**level) if task in RADIAL else None,
            'radius_axis':'operand_magnitude' if task in RADIAL else 'native_task_scale'}


def _fixed_cases(state,phase,focus=None,per_cell=1):
    cells=tasks.task_order(state['policy']['source_levels'] if phase=='samples' else state['policy']['levels'])
    seed=state['policy']['seed']+39141
    deck=[]
    for task,level in cells:
        for i in range(per_cell):
            band='edge' if task in RADIAL else 'native'
            row=_make(state,task,level,85_000_000+97*i+level*23,seed,band)
            row['origin']='fixed_anchor'
            deck.append(row)
    return deck


def _fresh_cases(state,phase,check_index,focus=None,count=18,extra_levels=0):
    levels=state['policy'].get('source_levels',state['policy'].get('levels'))
    cells=tasks.task_order(levels)
    seed=state['policy']['seed']+81391
    rows=[]
    for j in range(count):
        if phase=='systematic' and focus is not None:
            task,level=focus
        else:
            task,level=cells[(check_index*count+j)%len(cells)]
        # Only the post-loss balance phase explores new difficulty levels.
        # Its fixed reference anchors retain the original L0/L1 population.
        if phase=='balance' and extra_levels>0:
            level+=extra_levels
        band=BANDS[(check_index+j)%len(BANDS)][0] if task in RADIAL else 'native'
        # Odd stride preserves different task submodes, offset by check.
        index=86_000_000+(check_index*count+j)*97+17
        row=_make(state,task,level,index,seed,band)
        row['origin']='frontier_probe'
        rows.append(row)
    return rows


def _case_export(row,phase,step,lineage,kind):
    return {'schema':SCHEMA,'kind':kind,'case_key':_key(row),
            'case_id':row.get('id'),'source':row['source'],'target':row['target'],
            'task':row['task'],'level':row['level'],
            'terminal_kind':row.get('terminal_kind'), 'case_metadata':row.get('metadata',{}),
            'phase':phase,'observed_step':step,'parent_sha256':lineage,
            'origin':row.get('origin'),'band':row.get('band','historical'),
            'radius_axis':row.get('radius_axis'),
            'nominal_operand_anchor':row.get('nominal_operand_anchor'),
            'probe_operand_boundary':row.get('probe_operand_boundary'),
            'probe_seed':row.get('probe_seed'),'probe_index':row.get('probe_index')}


def _archive_import(out,previous,phase,lineage,limit=10000):
    """Preserve earlier observed failures without pretending they remain wrong."""
    destination=Path(out)/'glass_hard_candidates.jsonl'
    sources=[]
    if previous is not None:
        sources.extend((Path(previous)/'discovered_errors.jsonl',
                        Path(previous)/'glass_hard_candidates.jsonl'))
    sources.extend((Path(out)/'discovered_errors.jsonl',
                    Path(out)/'mixed_discovered_errors.jsonl'))
    seen={x.get('case_key') for x in _read(destination)}
    added=0
    for source in sources:
        if source==destination:continue
        for item in _read(source):
            if len(seen)>=limit:return added
            if not all(isinstance(item.get(k),str) and item[k] for k in ('source','target')):continue
            if item.get('task') not in tasks.TASKS or not isinstance(item.get('level'),int):continue
            key=hashlib.sha256((item['source']+'\0'+item['target']).encode('utf8')).hexdigest()
            if key in seen:continue
            seen.add(key)
            obj={'schema':SCHEMA,'kind':'historical_unrechecked','case_key':key,
                 'case_id':item.get('case_id'),'source':item['source'],'target':item['target'],
                 'task':item['task'],'level':item['level'],'phase':item.get('phase',phase),
                 'observed_step':item.get('observed_step',item.get('step')),
                 'origin':'historical_discovery','band':'historical',
                 'radius_axis':'unknown','parent_sha256':lineage,
                 'terminal_kind':item.get('terminal_kind'),
                 'case_metadata':item.get('case_metadata',{})}
            fr._append(destination,obj);added+=1
    return added


def _history(path,step,lineage):
    events={}
    for r in _read(path):
        if r.get('schema')!=SCHEMA or r.get('parent_sha256')!=lineage:
            raise RuntimeError('COMPETENCY_LEDGER_LINEAGE_MISMATCH')
        n=r.get('step')
        if not isinstance(n,int):raise RuntimeError('COMPETENCY_LEDGER_INVALID_STEP')
        if n<=step:
            if n in events and events[n]!=r:raise RuntimeError('COMPETENCY_LEDGER_DUPLICATE_CONFLICT')
            events[n]=r
    return [events[n] for n in sorted(events)]


def _paired(pre,now):
    common=pre.keys() & now.keys()
    gained=sum(not pre[k] and now[k] for k in common)
    lost=sum(pre[k] and not now[k] for k in common)
    return {'paired_tested':len(common),'gained':gained,'lost':lost,
            'net':gained-lost}


def _rolling(history,window=4):
    checks=[r['paired'] for r in history if r['paired'].get('paired_tested',r['paired'].get('paired',0))>0]
    selected=checks[-window:]
    result={'checks':len(selected), 'window':window,
            'gained':sum(r['gained'] for r in selected),
            'lost':sum(r['lost'] for r in selected)}
    result['net']=result['gained']-result['lost']
    result['flat_or_negative']=len(selected)==window and result['net']<=0
    return result


def observe(*,bb,tok,state,args,out,phase,focus=None,previous=None,
            per_cell=1,new_per_check=18,replay_per_check=24,postprocess=None,
            pinned_cases=(),frontier_extra_levels=0):
    """Scores current weights, records all hard cases, measures *paired* changes.

    The probe deck and case-order are deterministic and separate from training
    RNG; only the model's read-only greedy decoder is called. A new case cannot
    count as a gain until an earlier evaluation of that same case exists.
    """
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    step=int(state['step']);phase_step=int(state.get('phase_step',state.get('task_step',0)))
    lineage=(state.get('parent_sha256') if phase=='samples' else state.get('seed_sha256'))
    if not lineage:raise RuntimeError('COMPETENCY_LEDGER_SOURCE_IDENTITY_MISSING')
    history=_history(out/'competency_checks.jsonl',step,lineage)
    model_hash=fr._lora_hash(bb) if bb is not None else None
    for item in history:
        if item['step']==step:
            if item.get('model_lora_sha256')!=model_hash:
                raise RuntimeError('COMPETENCY_LEDGER_SAME_STEP_MODEL_DRIFT')
            fr._event('QWEN_NET_COMPETENCY_REPLAY',item['summary'])
            return item['summary']
    prior=[r for r in history if r['step']<step]
    previous_observations={}
    for ev in prior:
        previous_observations.update(ev['observations'])
    added=_archive_import(out,previous,phase,lineage)
    hard_archive=_read(out/'glass_hard_candidates.jsonl')
    replay_archive=_read(out/'glass_replay_candidates.jsonl')
    bank={}
    # Revisit archived historical mistakes and correct cases, not just fresh
    # samples. Never claim this is a DEV split or a complete mastery census.
    for source in hard_archive+replay_archive:
        if not all(k in source for k in ('source','target','task','level')):continue
        bank.setdefault(source['case_key'],source)
    priority=sorted(bank.values(),key=lambda r:(r['case_key']))
    if priority:
        start=(len(prior)*replay_per_check)%len(priority)
        priority=(priority[start:]+priority[:start])[:replay_per_check]
    rows=[]
    # Fixed anchors are evaluated identically at each check; we never silently
    # call newly introduced correct answers "newly acquired".
    rows.extend(_fixed_cases(state,phase,focus,per_cell))
    rows.extend(_fresh_cases(state,phase,len(prior),focus,new_per_check,
                             extra_levels=frontier_extra_levels))
    rows.extend(pinned_cases)
    for entry in priority:
        rows.append({**entry,'id':entry.get('case_id',entry['case_key']),
                     'origin':'archived_replay'})
    unique={}
    for row in rows:unique.setdefault(_key(row),row)
    observations={};details={};groups=defaultdict(lambda:{'correct':0,'wrong':0})
    sampled_fixed=0
    for row in unique.values():
        got=fr._greedy_one(bb,tok,row,args)
        ok,parsed=ladder.correctness(row['source'],row['target'],got)
        key=_key(row);observations[key]=bool(ok)
        details[key]={'task':row['task'],'level':row['level'],
                      'band':row.get('band','historical')}
        label=f"{row['task']}:L{row['level']}:{row.get('band','historical')}"
        groups[label]['correct' if ok else 'wrong']+=1
        archive_row=_case_export(row,phase,step,lineage,'mastered' if ok else 'hard')
        archive_row['greedy']=got;archive_row['parsed']=parsed
        target=out/('glass_replay_candidates.jsonl' if ok else 'glass_hard_candidates.jsonl')
        _append_once(target,archive_row,lambda x:x['case_key'])
        if row.get('origin')=='fixed_anchor':sampled_fixed+=1
    # Paired changes are measured against last observation of the *same*
    # problem, not against arbitrary independently sampled task averages.
    paired=_paired(previous_observations,observations)
    last_step=prior[-1]['step'] if prior else step
    delta=max(0,step-last_step)
    paired['net_per_100_steps']=100*paired['net']/delta if delta else None
    by_cell=defaultdict(lambda:{'gained':0,'lost':0,'paired':0})
    by_band=defaultdict(lambda:{'gained':0,'lost':0,'paired':0})
    for key,now in observations.items():
        if key not in previous_observations:continue
        d=details[key];before=previous_observations[key]
        cell=f"{d['task']}:L{d['level']}"
        band=f"{cell}:{d['band']}"
        for bucket in (by_cell[cell],by_band[band]):
            bucket['paired']+=1
            bucket['gained']+=int(not before and now)
            bucket['lost']+=int(before and not now)
    for bucket in list(by_cell.values())+list(by_band.values()):
        bucket['net']=bucket['gained']-bucket['lost']
    fixed_now={_key(r):observations[_key(r)] for r in rows
               if r.get('origin')=='fixed_anchor' and _key(r) in observations}
    fixed_prev={k:previous_observations[k] for k in fixed_now if k in previous_observations}
    anchor=_paired(fixed_prev,fixed_now)
    events=prior+[{'paired':paired}]
    rolling=_rolling(events)
    focus_key=f"{focus[0]}:L{focus[1]}" if focus is not None else None
    if focus_key is not None:
        focus_events=[{'paired':e['summary'].get('paired_by_cell',{}).get(focus_key,
                      {'paired':0,'gained':0,'lost':0})} for e in prior
                      if e.get('summary',{}).get('focus_key')==focus_key]
        current_focus=by_cell.get(focus_key,{'paired':0,'gained':0,'lost':0})
        focus_events.append({'paired':{'paired_tested':current_focus['paired'],
                             'gained':current_focus['gained'],'lost':current_focus['lost']}})
        focus_rolling=_rolling(focus_events)
    else:focus_rolling=rolling
    summary={'schema':SCHEMA,'phase':phase,'step':step,'phase_step':phase_step,
             'focus_key':focus_key, 'focus_recent':focus_rolling,
             'fixed_anchor_correct':sum(fixed_now.values()),
             'fixed_anchor_tested':len(fixed_now),
             'probed':len(unique),'fixed_anchors':sampled_fixed,
             'fresh_probes':sum(row.get('origin')=='frontier_probe' for row in unique.values()),
             'frontier_extra_levels':frontier_extra_levels,
             'pinned_cases':len(pinned_cases),
             'replayed':sum(row.get('origin')=='archived_replay' for row in unique.values()),
             'paired':paired,'fixed_anchor_paired':anchor,'recent':rolling,
             'wrong_current':sum(not x for x in observations.values()),
             'hard_archive_size':len(_read(out/'glass_hard_candidates.jsonl')),
             'replay_archive_size':len(_read(out/'glass_replay_candidates.jsonl')),
             'imported_historical':added,
             'by_task_band':{key:val for key,val in sorted(groups.items())},
             'paired_by_cell':{key:val for key,val in sorted(by_cell.items())},
             'paired_by_band':{key:val for key,val in sorted(by_band.items())},
             'nonretroactive':len(prior)==0,
             'net_rate_is_sampled_not_total_frontier_competence':True}
    event={'schema':SCHEMA,'parent_sha256':lineage,'step':step,'phase_step':phase_step,
           'model_lora_sha256':model_hash,
           'observations':observations,'paired':paired,'summary':summary}
    _append_once(out/'competency_checks.jsonl',event,lambda x:x['step'])
    fr._event('QWEN_NET_COMPETENCY',summary)
    return summary


def export_existing(*,systematic_output,samples_output,diagnostic_report=None):
    """No-GPU migration of existing raw/discovered error evidence for glasses.

    This is an archive operation, NOT a new accuracy measurement. Historic
    failures are never claimed still incorrect until rechecked against the
    exact frozen backbone. Safe to rerun; never rewrites checkpoints.
    """
    systematic=Path(systematic_output).resolve()
    samples=Path(samples_output).resolve()
    if not systematic.is_dir() or not samples.is_dir():
        raise FileNotFoundError('COMPETENCY_EXPORT_NEEDS_SYSTEMATIC_AND_SAMPLES_DIRECTORIES')
    added=_archive_import(samples,systematic,'samples','legacy_import')
    diagnostic_added=0
    if diagnostic_report:
        path=Path(diagnostic_report)/'hard_candidates.jsonl'
        if not path.is_file():raise FileNotFoundError(path)
        known={x['case_key'] for x in _read(samples/'glass_hard_candidates.jsonl')}
        for item in _read(path):
            if not all(isinstance(item.get(k),str) and item[k] for k in ('source','target','task')):
                continue
            if item.get('task') not in tasks.TASKS or not isinstance(item.get('level'),int):
                continue
            key=hashlib.sha256((item['source']+'\0'+item['target']).encode()).hexdigest()
            if key in known:continue
            known.add(key)
            fr._append(samples/'glass_hard_candidates.jsonl',{
                'schema':SCHEMA,'kind':'diagnostic_unrechecked','case_key':key,
                'case_id':item.get('id'),'task':item['task'],'level':item['level'],
                'source':item['source'],'target':item['target'],
                'origin':'diagnostic','band':item.get('band'),
                'radius_axis':item.get('radius_axis'),
                'target_operand_boundary':item.get('target_operand_boundary'),
                'baseline_loss':item.get('baseline_loss'),
                'baseline_correct':item.get('baseline_correct'),
                'observed_step':None,'parent_sha256':None})
            diagnostic_added+=1
    result={'schema':SCHEMA,'status':'EXPORTED_NO_TRAINING',
            'old_raw_errors_imported':added,'diagnostic_errors_imported':diagnostic_added,
            'total_hard_candidates':len(_read(samples/'glass_hard_candidates.jsonl')),
            'output':str(samples/'glass_hard_candidates.jsonl'),
            'requires_current_frozen_backbone_recheck_before_glass_training':True,
            'not_a_net_competency_history':True}
    fr._event('QWEN_COMPETENCY_EXPORT',result)
    return result


def main():
    import argparse
    p=argparse.ArgumentParser(description='Archive historical Qwen frontier failures for glass training, without re-probing.')
    p.add_argument('--systematic-output',required=True)
    p.add_argument('--samples-output',required=True)
    p.add_argument('--diagnostic-report',default=None)
    a=p.parse_args()
    export_existing(systematic_output=a.systematic_output,samples_output=a.samples_output,
                    diagnostic_report=a.diagnostic_report)


if __name__=='__main__':main()
