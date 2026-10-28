#!/usr/bin/env python3
"""Active HybridLink rung frontier: generated-state stress, constrained repair, retention.

Never changes frozen Qwen, Answer Detector, numeric heads, or input checkpoints.
Uses *recorded* transitions, never an invented oracle chain. A pass certifies only
its explicitly recorded/audited cases, not all possible arithmetic or latent tasks.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import random
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from tools import nanojev_hybridlink_t01_retention_cutover as t01
from tools import nanojev_hybridlink_t00_curriculum_repair as t00
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_latent_diagnostics as diag
from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
from tools import nanojev_hybridlink_rung_reduction as base
from tools import nanojev_qwen_numeric_dual_cutover as parent

SCHEMA = 'nanojev.hybridlink.active-frontier-repair.v1'


def _emit(event, obj):
    print(event+'='+json.dumps(obj, sort_keys=True, default=str), flush=True)


def _key(rung, chain):
    return (rung, chain[0], tuple(chain))


def _chain_digest(chain):
    return hashlib.sha256(json.dumps(chain, ensure_ascii=False).encode('utf8')).hexdigest()[:20]


def _number_features(text):
    ns=[int(x) for x in re.findall(r'(?<![A-Za-z])[-+]?\d+', text)]
    return {'max_operand_abs':max((abs(x) for x in ns),default=0),
            'negative_numbers':sum(x<0 for x in ns),
            'digits':max((len(str(abs(x))) for x in ns),default=0),
            'parentheses':text.count('('), 'operators':text.count('+')+text.count('-')}


def _priority(chain):
    x=_number_features(chain[0]);return (x['max_operand_abs'],x['parentheses'],x['digits'],chain[0])


def windows(chains, rung, cap, seed=42257, must_include=()):
    """Select only actual chain suffixes; preserve historical trouble and diversity."""
    if cap<1:raise ValueError('FRONTIER_CASE_CAP_MUST_BE_POSITIVE')
    eligible={tuple(ch[-(rung+2):]) for ch in chains if len(ch)-1>=rung+1}
    if not eligible:return []
    mandatory=[ch for ch in eligible if ch[0] in set(must_include)]
    candidates=sorted(eligible,key=lambda ch:(_priority(ch),ch),reverse=True)
    # Include boundaries, small/easy controls, and reproducible random interiors.
    rng=random.Random(seed+103*rung)
    easy=sorted(eligible,key=lambda ch:(_priority(ch),ch))
    pool=list(eligible);pool.sort();rng.shuffle(pool)
    result=[];seen=set()
    for group in (sorted(mandatory),candidates[:max(1,cap//4)],easy[:max(1,cap//4)],pool,candidates):
        for ch in group:
            if ch not in seen:seen.add(ch);result.append(list(ch))
            if len(result)>=cap:return result
    return result



def inject_accepted_gate_cases(selected,dev_chains,cap):
    """The original 12-case T00 and T01 accepted gates must never be dropped."""
    guaranteed={0:t00._selected_stage_chains(dev_chains,0,12),
                1:t01._selected(dev_chains,12)}
    for rung,old in guaranteed.items():
        if len(old)>cap:
            raise ValueError('FRONTIER_CASE_CAP_BELOW_ACCEPTED_T01_BASELINE')
        seen=set();result=[]
        for item in old+selected[rung]:
            ch=tuple(item)
            if ch not in seen:seen.add(ch);result.append(list(ch))
            if len(result)>=cap:break
        selected[rung]=result
    return selected


def discover_windows(chains_by_split,max_rung,cap,seed,hard_sources=()):
    combined=[]
    for part in ('dev','train'):
        combined.extend(chains_by_split[part])
    out={r:windows(combined,r,cap,seed,hard_sources) for r in range(max_rung+1)}
    return out


class EvidenceBank:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(self.path))
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cases (
          rung INTEGER NOT NULL, source TEXT NOT NULL, chain_sha TEXT NOT NULL,
          chain_json TEXT NOT NULL, first_label TEXT, observations INTEGER NOT NULL DEFAULT 0,
          fragile INTEGER NOT NULL DEFAULT 0, historical INTEGER NOT NULL DEFAULT 0,
          failures INTEGER NOT NULL DEFAULT 0, threatened INTEGER NOT NULL DEFAULT 0,
          repaired INTEGER NOT NULL DEFAULT 0, worst_margin REAL, last_status TEXT,
          PRIMARY KEY(rung, source, chain_sha));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, rung INTEGER NOT NULL,
          source TEXT NOT NULL, chain_sha TEXT NOT NULL,
          event TEXT NOT NULL, cycle INTEGER NOT NULL, group_name TEXT,
          expected_depth INTEGER, observed_steps INTEGER, status TEXT,
          correct INTEGER, final_cosine REAL, min_detector_margin REAL,
          detail_json TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_case ON events(rung,source,chain_sha);
        ''')

    def record(self,rung,chain,event,cycle,row=None,group_name=None,detail=None):
        row=row or {}; digest=_chain_digest(chain);margin=row.get('min_detector_margin')
        self.db.execute('''INSERT OR IGNORE INTO cases
            (rung,source,chain_sha,chain_json,first_label) VALUES(?,?,?,?,?)''',
            (rung,chain[0],digest,json.dumps(chain,ensure_ascii=False),event))
        increments={'failures':int(event=='failure'),'threatened':int(event=='threatened'),
                    'repaired':int(event=='repaired'),'fragile':int(event=='fragile'),
                    'historical':int(event=='historical_loss')}
        self.db.execute('''UPDATE cases SET observations=observations+1,
            failures=failures+?,threatened=threatened+?,repaired=repaired+?,fragile=fragile+?,historical=historical+?,
            worst_margin=CASE WHEN ? IS NULL THEN worst_margin
                         WHEN worst_margin IS NULL THEN ? ELSE min(worst_margin,?) END,
            last_status=? WHERE rung=? AND source=? AND chain_sha=?''',
            (increments['failures'],increments['threatened'],increments['repaired'],increments['fragile'],increments['historical'],
             margin,margin,margin,event,rung,chain[0],digest))
        self.db.execute('''INSERT INTO events(rung,source,chain_sha,event,cycle,group_name,
               expected_depth,observed_steps,status,correct,final_cosine,min_detector_margin,detail_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (rung,chain[0],digest,event,cycle,group_name,len(chain)-1,row.get('steps'),
             row.get('status'),int(row['correct']) if 'correct' in row else None,
             row.get('final_cosine'),margin,json.dumps(detail or row,ensure_ascii=False,default=str)))
        self.db.commit()

    def risk(self,rung,chain):
        row=self.db.execute('SELECT failures,threatened,worst_margin,fragile,historical FROM cases WHERE rung=? AND source=? AND chain_sha=?',
             (rung,chain[0],_chain_digest(chain))).fetchone()
        return (row[0]*4+row[1]*2+row[3]+row[4]*3+(1/(.02+max(0,row[2])) if row[2] is not None else 0)) if row else 0

    def summary(self):
        return dict(zip(('cases','failures','threatened','repaired','fragile','historical'),self.db.execute(
            'SELECT count(*),coalesce(sum(failures),0),coalesce(sum(threatened),0),coalesce(sum(repaired),0),coalesce(sum(fragile),0),coalesce(sum(historical),0) FROM cases').fetchone()))

    def export(self,directory):
        with (Path(directory)/'hard_cases.csv').open('w',newline='',encoding='utf8') as f:
            writer=csv.writer(f);writer.writerow(('rung','source','failures','threatened','repaired','fragile','historical','worst_margin','last_status'))
            writer.writerows(self.db.execute('''SELECT rung,source,failures,threatened,repaired,fragile,historical,worst_margin,last_status
                FROM cases WHERE failures>0 OR threatened>0 OR fragile>0 OR historical>0
                ORDER BY failures*4+threatened*2+fragile+historical*3 DESC'''))
        counts=defaultdict(lambda:[0,0,0])
        for src,fails,threats,fragile in self.db.execute(
                'SELECT source,failures,threatened,fragile FROM cases'):
            for raw in re.findall(r'(?<![A-Za-z])[-+]?\d+',src):
                key=int(raw);counts[key][0]+=fails;counts[key][1]+=threats;counts[key][2]+=fragile
        with (Path(directory)/'hard_numbers.csv').open('w',newline='',encoding='utf8') as f:
            writer=csv.writer(f);writer.writerow(('number','failure_occurrences','threat_occurrences','fragile_occurrences'))
            writer.writerows((n,*v) for n,v in sorted(counts.items(),
                key=lambda x:(-(x[1][0]*4+x[1][1]*2+x[1][2]),-abs(x[0]))))

    def close(self):self.db.close()


def historical_t00_sources(directory):
    p=Path(directory)/'history.json'
    if not p.is_file():return []
    try:history=json.loads(p.read_text(encoding='utf8'))
    except (OSError,ValueError):return []
    sources=set()
    for rec in history:
        if isinstance(rec,dict):
            retention=rec.get('retention') or {}
            sources.update(x for x in retention.get('lost_sources',[]) if isinstance(x,str))
    return sorted(sources)


def _record_grade(raw,chain,references,numeric,scale,threshold):
    """Grade after autonomous rollout; never pass reference states into generation."""
    expected=len(chain)-1
    history=[];margins=[];cosines=[]
    for i,rec in enumerate(raw['generated']):
        gold=chain[i+1] if i+1<len(chain) else None
        cosine=diag._cos(rec['_vector'],references[gold]) if gold in references else None
        if cosine is not None:cosines.append(cosine)
        p=float(rec['p_answer']);margins.append((threshold-p) if i+1<expected else p-threshold)
        history.append({'step':i+1,'action':rec['action'],'p_answer':p,
                        'relative_drift':rec['relative_drift'],
                        'target_vector_cosine':cosine,'gold_target':gold})
    complete=raw['status']=='ANSWER' and raw['steps']==expected
    last=history[-1]['target_vector_cosine'] if len(history)==expected else None
    return {'source':chain[0],'rung':expected-1,'chain':chain,
            'status':raw['status'],'steps':raw['steps'],'expected_steps':expected,
            'correct':complete,'early_false_stop':raw['status']=='ANSWER' and raw['steps']<expected,
            'late_exit':raw['status']=='ANSWER' and raw['steps']>expected,
            'final_cosine':last,'mean_step_cosine':sum(cosines)/len(cosines) if cosines else None,
            'min_detector_margin':min(margins) if margins else None,'history':history}


def evaluate_one(bb,tok,link,det,numeric,chain,references,args,scale):
    # Never pass chain targets to generate the trace.
    raw=diag.build_trace(bb,tok,link,det,numeric,[chain[0]],args.device,
             args.max_tokens,scale,args.answer_threshold,args.drift_threshold,
             args.stall_patience,len(chain)-1+args.overrun_steps)
    return _record_grade(raw,chain,references,numeric,scale,args.answer_threshold)


def audit_cases(bb,tok,link,det,numeric,cases,refs,args,scale):
    return {_key(r,ch):evaluate_one(bb,tok,link,det,numeric,ch,refs,args,scale)
            for r,ch in cases}


def passed_case(row,quality_floor):
    return bool(row['correct']) and (row.get('final_cosine') is not None and row['final_cosine']>=quality_floor)


def evaluate_safety(before,after,quality_floor,max_cosine_drop):
    """Fail closed: every prior success stays successful and retains state quality."""
    if before.keys()!=after.keys():raise RuntimeError('FRONTIER_SAFETY_CASE_SET_DRIFT')
    regressions=[];new_repairs=[];quality_drops=[]
    for k,old in before.items():
        new=after[k]
        old_good=passed_case(old,quality_floor);new_good=passed_case(new,quality_floor)
        if old_good and not new_good:regressions.append(k)
        elif not old_good and new_good:new_repairs.append(k)
        if old_good and new_good and old['final_cosine']-new['final_cosine']>max_cosine_drop:
            quality_drops.append(k)
    return {'regressions':regressions,'new_repairs':new_repairs,'quality_drops':quality_drops,
            'safe':not regressions and not quality_drops and bool(new_repairs)}


def choose_rehearsal(current,bank,limit,quality_floor):
    good=[(k,r) for k,r in current.items() if passed_case(r,quality_floor)]
    good.sort(key=lambda x:(-bank.risk(x[0][0],x[1]['chain']),
                  x[1].get('min_detector_margin') if x[1].get('min_detector_margin') is not None else 1,
                  x[1].get('final_cosine') if x[1].get('final_cosine') is not None else 1))
    return good[:limit]


def param_groups(link):
    """Smallest existing generator components, no frozen backbone or detector."""
    groups=defaultdict(list)
    for name,p in link.named_parameters():
        group='link.'+name.split('.')[0];groups[group].append((name,p))
    return dict(groups)


def rank_groups(link,faults,lookups,bb,tok,det,args):
    """Gradient magnitude on actual failing transitions (no expert/donor)."""
    import torch
    link.requires_grad_(True);link.train()
    link.zero_grad(set_to_none=True)
    for chain in faults[:min(3,len(faults))]:
        src=chain[0];h=lookups[src].to(args.device).reshape(1,-1)
        loss,_=loop.train_suffix(bb,tok,link,det,chain,lookups,h,args.device,
                                 args.max_tokens,args.completion_weight)
        (loss/len(faults[:min(3,len(faults))])).backward()
    weights={}
    for group,entries in param_groups(link).items():
        weights[group]=sum(float(p.grad.detach().float().square().sum())
                           for _,p in entries if p.grad is not None)
    link.zero_grad(set_to_none=True);link.eval()
    return sorted(weights,key=lambda g:(-weights[g],g))


def _blend(candidate,baseline,alpha):
    import torch
    with torch.no_grad():
        for (n,p),(_,old) in zip(candidate.named_parameters(),baseline.named_parameters()):
            p.copy_(old+alpha*(p-old))
    return candidate


def _distill_loss(bb,tok,link,chain,target_states,lookup,args):
    """Keep actual previously learned intermediate states, not just final labels."""
    import torch
    import torch.nn.functional as F
    context=loop.token_embeddings(bb,tok,chain[0],args.device,args.max_tokens)
    h=lookup[chain[0]].to(args.device).reshape(1,-1)
    losses=[]
    for vec in target_states:
        context,h=loop.feedback_step(bb,link,context,h,args.device)
        target=vec.to(args.device).view_as(h)
        losses.append(1-F.cosine_similarity(h.float(),target.float(),dim=-1).mean()
                      +.05*F.smooth_l1_loss(h.float(),target.float()))
    return sum(losses)/len(losses)


def anchors_for(bb,tok,link,det,numeric,entries,args,scale):
    anchors={}
    for k,row in entries:
        ch=row['chain'];raw=diag.build_trace(bb,tok,link,det,numeric,[ch[0]],
                    args.device,args.max_tokens,scale,args.answer_threshold,
                    args.drift_threshold,args.stall_patience,len(ch)-1)
        if len(raw['generated'])==len(ch)-1:
            anchors[k]=[x['_vector'].detach().cpu() for x in raw['generated']]
    return anchors


def _checkpoint(path,cfg,link,det,rung,cycle,status,counts):
    frozen._atomic_tensor(path,{
        'schema':loop.SCHEMA,'config_hash':base.digest(cfg),
        'link':{k:v.detach().cpu().clone() for k,v in link.state_dict().items()},
        'detector':{k:v.detach().cpu().clone() for k,v in det.state_dict().items()},
        'training_schema':SCHEMA,'accepted_rung':rung if status=='certified' else None,
        'status':'FRONTIER_GATED_NOT_FULL_LATENT_CERTIFIED' if status=='certified' else 'FRONTIER_PARTIAL_NOT_PROMOTED',
        'cycle':cycle,'audit':counts,'trained_generator_only':True})


def verify_input(path,args,prepared):
    ck=t00._verified_link_checkpoint(path,args,prepared)
    if ck.get('training_schema')==t01.SCHEMA:
        if ck.get('accepted_rung')!=1 or ck.get('status')!='T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED':
            raise RuntimeError('FRONTIER_REQUIRES_ACCEPTED_T01')
    elif ck.get('training_schema')==SCHEMA:
        if ck.get('status') not in ('FRONTIER_GATED_NOT_FULL_LATENT_CERTIFIED','FRONTIER_PARTIAL_NOT_PROMOTED'):
            raise RuntimeError('FRONTIER_UNRECOGNIZED_REPAIR_CHECKPOINT')
    else:raise RuntimeError('FRONTIER_REQUIRES_T01_OR_FRONTIER_CHECKPOINT')
    import torch
    if any(not torch.equal(p.detach().cpu(),ck['detector'][k]) for k,p in prepared['det'].state_dict().items()):
        raise RuntimeError('FRONTIER_DETECTOR_CHECKPOINT_DRIFT')
    return ck


def _configuration(args,prepared,source_path):
    return {'schema':loop.SCHEMA,'frontier_schema':SCHEMA,
            'stage1_sha256':prepared['parent_hash'],'detector_sha256':args._det_sha,
            'text_sha256':parent.file_hash(args.text_checkpoint),
            'frozen_backbone_sha256':prepared['frozen_hash'],
            'source_sha256':prepared['source_hash'],
            'deck_sha256':prepared['parent_cfg']['deck_sha256'],
            'source_checkpoint_sha256':parent.file_hash(source_path),
            'source_checkpoint':str(source_path),'seed':args.seed,
            'max_tokens':args.max_tokens,'answer_threshold':args.answer_threshold,
            'drift_threshold':args.drift_threshold,'stall_patience':args.stall_patience,
            'max_rung':args.max_rung,'cases_per_rung':args.cases_per_rung,
            'min_gold_cosine':args.min_gold_cosine,'max_cosine_drop':args.max_cosine_drop,
            'train_only_generator':True,'no_oracle_during_rollout':True}


def _write_json(path,obj):
    base._atomic_json(Path(path),obj)


def _store_audit(out,rung,cycle,rows):
    """A human-inspectable per-case, per-hop record; no hidden tensors serialized."""
    record={'schema':SCHEMA,'rung':rung,'cycle':cycle,
            'cases':[rows[k] for k in sorted(rows)]}
    _write_json(out/'latest_audit.json',record)
    with (out/'audit_history.jsonl').open('a',encoding='utf8') as fh:
        fh.write(json.dumps(record,sort_keys=True,ensure_ascii=False)+'\n')


def _load_prior_selected(source_checkpoint,proposed,max_rung):
    p=Path(source_checkpoint).parent/'selected_cases.json'
    if not p.exists():return proposed,False
    old=json.loads(p.read_text(encoding='utf8'))
    for rung_text,rows in old.items():
        rung=int(rung_text)
        if rung<=max_rung:
            if any(len(ch)!=rung+2 for ch in rows):
                raise RuntimeError('FRONTIER_PRIOR_CASES_INVALID_DEPTH')
            proposed[rung]=rows
    return proposed,True


def _load_prior_successes(source_checkpoint,quality_floor):
    p=Path(source_checkpoint).parent/'audit_history.jsonl'
    if not p.is_file():return {}
    anchors={}
    with p.open('r',encoding='utf8') as fh:
        for line in fh:
            rec=json.loads(line)
            for row in rec.get('cases',[]):
                if passed_case(row,quality_floor):
                    k=_key(row['rung'],row['chain'])
                    anchors.setdefault(k,row)
    return anchors


def _import_previous_bank(source_checkpoint,output):
    source=Path(source_checkpoint).parent/'hard_cases.sqlite3'
    destination=Path(output)/'hard_cases.sqlite3'
    if not source.is_file():return False
    destination.parent.mkdir(parents=True,exist_ok=True)
    # SQLite online backup carries WAL-consistent data without touching source.
    with sqlite3.connect(str(source)) as old,sqlite3.connect(str(destination)) as new:
        old.backup(new)
    return True


def _audit_current(bb,tok,link,det,num,all_cases,refs,args,scale):
    import torch
    with torch.no_grad():return audit_cases(bb,tok,link,det,num,all_cases,refs,args,scale)


def _counts(current,quality_floor):
    return {'cases':len(current),'correct':sum(passed_case(row,quality_floor) for row in current.values()),
            'incorrect':sum(not passed_case(row,quality_floor) for row in current.values()),
            'wrong_stop':sum(not row['correct'] for row in current.values()),
            'low_quality':sum(row['correct'] and not passed_case(row,quality_floor) for row in current.values())}


def attempt_repair(bb,tok,link,det,num,all_cases,current,retention_baseline,refs,args,scale,bank,rung,cycle):
    import torch
    failures=[row['chain'] for row in current.values() if not passed_case(row,args.min_gold_cosine)
              and row['rung']==rung]
    if not failures:return None,current,None
    rankings=rank_groups(link,failures,refs,bb,tok,det,args)
    _emit('HYBRID_FRONTIER_GROUPS',{'rung':rung,'cycle':cycle,'ranked_groups':rankings,'failures':len(failures)})
    old=copy.deepcopy(link).to(args.device).eval()
    # Rehearse fragile successes and known threatened cases, rotating each attempt.
    guards=choose_rehearsal(current,bank,args.replay_limit,args.min_gold_cosine)
    anchors=anchors_for(bb,tok,old,det,num,guards,args,scale)
    rng=random.Random(args.seed+1019*rung+cycle)
    limits=[1,2,3,len(rankings)]
    for group_count in sorted(set(min(len(rankings),n) for n in limits)):
        selected=set(rankings[:group_count]);candidate=copy.deepcopy(old).to(args.device)
        for name,p in candidate.named_parameters():p.requires_grad_(('link.'+name.split('.')[0]) in selected)
        opt=torch.optim.AdamW([p for p in candidate.parameters() if p.requires_grad],lr=args.lr,weight_decay=0)
        for step in range(1,args.repair_steps+1):
            candidate.train()
            chain=rng.choice(failures)
            h=refs[chain[0]].to(args.device).reshape(1,-1)
            loss,_=loop.train_suffix(bb,tok,candidate,det,chain,refs,h,args.device,
                                     args.max_tokens,args.completion_weight)
            available=[(k,row) for k,row in guards if k in anchors]
            if available:
                k,row=rng.choice(available)
                loss=loss+args.preserve_weight*_distill_loss(bb,tok,candidate,row['chain'],anchors[k],refs,args)
            penalty=sum((p-b).float().square().sum() for p,b in zip(candidate.parameters(),old.parameters()) if p.requires_grad)
            loss=loss+args.l2*penalty
            opt.zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in candidate.parameters() if p.requires_grad],args.clip)
            opt.step()
            if step%args.check_every and step!=args.repair_steps:continue
            candidate.eval()
            # Safest candidate first; all accepted updates are fully audited.
            for alpha in args.scales:
                trial=_blend(copy.deepcopy(candidate).to(args.device),old,alpha).eval()
                # Fast guard catches most destructive edits before full audit.
                guard_cases=[(k[0],row['chain']) for k,row in guards[:args.quick_guards]]
                quick=_audit_current(bb,tok,trial,det,num,guard_cases,refs,args,scale) if guard_cases else {}
                threats=[k for k,new in quick.items() if k in current and
                         passed_case(current[k],args.min_gold_cosine) and
                         (not passed_case(new,args.min_gold_cosine) or
                          (current[k]['final_cosine']-new['final_cosine']>args.max_cosine_drop))]
                if threats:
                    for k in threats:
                        bank.record(k[0],list(k[2]),'threatened',cycle,quick[k],','.join(selected),
                                    {'step':step,'scale':alpha,'quick':True})
                    continue
                changed=_audit_current(bb,tok,trial,det,num,all_cases,refs,args,scale)
                verdict=evaluate_safety(current,changed,args.min_gold_cosine,args.max_cosine_drop)
                anchored={k:v for k,v in retention_baseline.items() if k in changed}
                lifetime=evaluate_safety(anchored,{k:changed[k] for k in anchored},
                                         args.min_gold_cosine,args.max_cosine_drop) if anchored else {'regressions':[],'quality_drops':[]}
                unsafe_keys=set(verdict['regressions']+verdict['quality_drops']+
                                lifetime['regressions']+lifetime['quality_drops'])
                for k in unsafe_keys:
                    bank.record(k[0],list(k[2]),'threatened',cycle,changed[k],','.join(selected),
                                {'step':step,'scale':alpha,'quick':False})
                if verdict['safe'] and not unsafe_keys:
                    for k in verdict['new_repairs']:
                        bank.record(k[0],list(k[2]),'repaired',cycle,changed[k],','.join(selected),
                                    {'step':step,'scale':alpha})
                    record={'rung':rung,'cycle':cycle,'step':step,'scale':alpha,
                            'groups':sorted(selected),'new_repairs':len(verdict['new_repairs']),
                            'new_regressions':0,'quality_drops':0,'counts':_counts(changed,args.min_gold_cosine)}
                    _emit('HYBRID_FRONTIER_REPAIR_ACCEPT',record)
                    return trial,changed,record
            _emit('HYBRID_FRONTIER_REPAIR_CHECK',{'rung':rung,'cycle':cycle,
                  'step':step,'groups':sorted(selected),'hard_cases':bank.summary()['cases'],
                  'accepted':False})
    return None,current,None


def _get_chains(args):
    p,_=parent.load_parent(args);s=parent.stage1()
    return {part:loop.chains_from_rows(parent._rows(s,p,part),allow_partial=(part=='dev'))
            for part in ('train','dev')}


def preview(args):
    chains=_get_chains(args)
    seen=discover_windows(chains,args.max_rung,args.cases_per_rung,args.seed)
    result={'schema':SCHEMA,'mode':'preview','checkpoint_loaded':False,
            'chain_depths':{p:sorted({len(ch)-1 for ch in part}) for p,part in chains.items()},
            'selected_cases_per_rung':{str(r):len(rows) for r,rows in seen.items()},
            'missing_rungs':[r for r,rows in seen.items() if not rows],
            'requires_real_chain_targets':True}
    _emit('HYBRID_FRONTIER_PREVIEW',result);return result


def run(args):
    import torch
    if not args.detector_checkpoint or not args.warm_start_t00 or not args.start_t01:
        raise ValueError('FRONTIER_REQUIRES_DETECTOR_T00_AND_T01_CHECKPOINTS')
    if args.cases_per_rung<1 or args.repair_cycles<0 or args.repair_steps<1 or args.max_rung<2:
        raise ValueError('FRONTIER_INVALID_BUDGET')
    protected=[Path(x).parent for x in (args.stage1_checkpoint,args.text_checkpoint,
                 args.detector_checkpoint,args.warm_start_t00,args.start_t01)]
    if args.cache_dir:protected.append(Path(args.cache_dir))
    parent._output_safe(args.output,protected)
    out=Path(args.output)
    if (out/'result.json').exists() or (out/'latest.pt').exists() or (out/'pending.pt').exists():
        raise RuntimeError('FRONTIER_OUTPUT_ALREADY_HAS_CHECKPOINT_USE_NEW_OUTPUT')
    prepared,num,det,dcfg=t00._prepare(args)
    prepared['det']=det
    original=verify_input(args.start_t01,args,prepared)
    bb=prepared['bb'];tok=prepared['tok'];det.eval().requires_grad_(False)
    num.eval().requires_grad_(False);bb.eval().requires_grad_(False)
    link=loop.make_link(int(bb.config.hidden_size),int(bb.get_input_embeddings().embedding_dim)).to(args.device)
    link.load_state_dict(original['link'],strict=True)
    torch.manual_seed(args.seed)
    original_det=original['detector']
    for k,p in det.state_dict().items():
        if not torch.equal(p.cpu(),original_det[k]):raise RuntimeError('FRONTIER_FROZEN_DETECTOR_DRIFT')
    chains={p:loop.chains_from_rows(prepared['deck'][p],allow_partial=(p=='dev')) for p in ('train','dev')}
    source_hist=historical_t00_sources(Path(args.start_t01).parent)
    chosen=discover_windows(chains,args.max_rung,args.cases_per_rung,args.seed,source_hist)
    chosen=inject_accepted_gate_cases(chosen,chains['dev'],args.cases_per_rung)
    chosen,restored_cases=_load_prior_selected(args.start_t01,chosen,args.max_rung)
    # Immutable Qwen reference states are cached under exact backbone lineage.
    names=sorted({s for rows in chosen.values() for ch in rows for s in ch})
    vectors,hit=frozen._load_or_encode(prepared['cache_dir'],'frontier-reference-v1',names,
                             bb,tok,prepared['s'],args,prepared['identity'])
    refs=dict(zip(names,vectors))
    out.mkdir(parents=True,exist_ok=True)
    cfg=_configuration(args,prepared,args.start_t01)
    _write_json(out/'run_config.json',cfg)
    _write_json(out/'selected_cases.json',{str(k):v for k,v in chosen.items()})
    imported_bank=_import_previous_bank(args.start_t01,out)
    bank=EvidenceBank(out/'hard_cases.sqlite3')
    for chain in chosen[0]:
        if chain[0] in source_hist:
            bank.record(0,chain,'historical_loss',0,detail={'origin':'T01 retention history'})
    history=[];best_rung=-1;partial=False;last_status=None
    retention_baseline=_load_prior_successes(args.start_t01,args.min_gold_cosine)
    try:
        for rung in range(args.max_rung+1):
            cases=chosen[rung]
            if not cases:
                last_status='no_recorded_chains'
                _emit('HYBRID_FRONTIER_BLOCKED',{'rung':rung,'reason':'NO_RECORDED_CHAINS_FOR_RUNG'})
                break
            # Every accepted lower rung becomes an immutable protected *case set*.
            casepairs=[(r,ch) for r in range(rung+1) for ch in chosen[r] if ch]
            current=_audit_current(bb,tok,link,det,num,casepairs,refs,args,dcfg['scale'])
            # Never silently begin from a checkpoint that has forgotten a
            # previously acquired case recorded in an earlier frontier run.
            earlier={k:v for k,v in retention_baseline.items() if k in current}
            lost=[k for k,v in earlier.items() if not passed_case(current[k],args.min_gold_cosine)
                  or v['final_cosine']-current[k]['final_cosine']>args.max_cosine_drop]
            if lost:
                last_status='inherited_retention_failure'
                _emit('HYBRID_FRONTIER_BLOCKED',{'rung':rung,
                       'reason':'INHERITED_RETENTION_FAILURE','lost_cases':len(lost)})
                break
            stats=_counts({k:v for k,v in current.items() if k[0]==rung},args.min_gold_cosine)
            _store_audit(out,rung,0,current)
            for k,row in current.items():
                if k[0]!=rung:continue
                if not passed_case(row,args.min_gold_cosine):
                    bank.record(rung,list(k[2]),'failure',0,row)
                elif row.get('min_detector_margin') is not None and row['min_detector_margin']<args.fragile_margin:
                    bank.record(rung,list(k[2]),'fragile',0,row)
            audit={'rung':rung,'cycle':0,'coverage':'recorded_cases_only',**stats,
                   'protected_prior_cases':sum(k[0]<rung for k in current)}
            history.append({'event':'audit',**audit})
            _emit('HYBRID_FRONTIER_AUDIT',audit)
            if any(not passed_case(row,args.min_gold_cosine) for k,row in current.items() if k[0]<rung):
                last_status='prior_rung_incorrect'
                _emit('HYBRID_FRONTIER_BLOCKED',{'rung':rung,'reason':'PRIOR_RUNG_FAILURE'})
                break
            # Immutable references for every previously acquired correct result.
            for k,row in current.items():
                if k not in retention_baseline and passed_case(row,args.min_gold_cosine):
                    retention_baseline[k]=row
            cycle=0
            while stats['incorrect'] and cycle<args.repair_cycles:
                trial,new,repair=attempt_repair(bb,tok,link,det,num,casepairs,current,retention_baseline,refs,args,
                                               dcfg['scale'],bank,rung,cycle)
                if trial is None:break
                link=trial;current=new;cycle+=1;partial=True
                for k,row in current.items():
                    if k not in retention_baseline and passed_case(row,args.min_gold_cosine):
                        retention_baseline[k]=row
                stats=_counts({k:v for k,v in current.items() if k[0]==rung},args.min_gold_cosine)
                _store_audit(out,rung,cycle,current)
                _checkpoint(out/'pending.pt',cfg,link,det,rung,cycle,'partial',stats)
                history.append({'event':'repair',**repair})
            if stats['incorrect']:
                last_status='stalled'
                _emit('HYBRID_FRONTIER_STALLED',{'rung':rung,'cycle':cycle,**stats})
                break
            # Re-audit the complete selected acquired set before promotion.
            final=_audit_current(bb,tok,link,det,num,casepairs,refs,args,dcfg['scale'])
            if any(not passed_case(row,args.min_gold_cosine) for row in final.values()):
                last_status='retention_reaudit_failed'
                _emit('HYBRID_FRONTIER_BLOCKED',{'rung':rung,'reason':last_status})
                break
            best_rung=rung
            if rung>=1:
                _checkpoint(out/'latest.pt',cfg,link,det,rung,cycle,'certified',stats)
            if (out/'pending.pt').exists():(out/'pending.pt').unlink()
            partial=False
            _emit('HYBRID_FRONTIER_ADVANCE',{'rung':rung,'repair_cycles':cycle,
                  'certification':'selected_recorded_cases_only',**stats})
            history.append({'event':'advance','rung':rung,**stats})
        else:last_status='target_reached'
        result={'schema':SCHEMA,'status':last_status,'highest_passed_rung':best_rung,
                'checkpoint':str(out/'latest.pt') if (out/'latest.pt').exists() else args.start_t01,
                'source_accepted_rung':1,
                'partial_checkpoint':str(out/'pending.pt') if partial else None,
                'hard_bank':str(bank.path),'hard_bank_summary':bank.summary(),
                'reference_cache_hit':hit,'prior_hard_bank_imported':imported_bank,
                'prior_selected_cases_restored':restored_cases,
                'original_t01_unmodified':True,
                'frozen_backbone':True,'frozen_detector':True,
                'scope':'selected recorded chains, not full latent certification'}
        _write_json(out/'history.json',history);_write_json(out/'result.json',result)
        bank.export(out)
        _emit('HYBRID_FRONTIER_RESULT',result)
        return result
    finally:bank.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','run'),default='preview')
    p.add_argument('--stage1-checkpoint',required=True);p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--detector-checkpoint');p.add_argument('--warm-start-t00');p.add_argument('--start-t01')
    p.add_argument('--cache-dir');p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_active_frontier_v1'))
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--max-rung',type=int,default=4);p.add_argument('--cases-per-rung',type=int,default=24)
    p.add_argument('--repair-cycles',type=int,default=6);p.add_argument('--repair-steps',type=int,default=150)
    p.add_argument('--check-every',type=int,default=50);p.add_argument('--quick-guards',type=int,default=12)
    p.add_argument('--replay-limit',type=int,default=64);p.add_argument('--overrun-steps',type=int,default=1)
    p.add_argument('--lr',type=float,default=.00005);p.add_argument('--clip',type=float,default=1.)
    p.add_argument('--l2',type=float,default=.01);p.add_argument('--completion-weight',type=float,default=.5)
    p.add_argument('--preserve-weight',type=float,default=2.0)
    p.add_argument('--scales',nargs='+',type=float,default=[.125,.25,.5,1.,.0625])
    p.add_argument('--min-gold-cosine',type=float,default=.60)
    p.add_argument('--max-cosine-drop',type=float,default=.025)
    p.add_argument('--fragile-margin',type=float,default=.05)
    p.add_argument('--max-tokens',type=int,default=192);p.add_argument('--encoder-batch',type=int,default=12)
    p.add_argument('--answer-threshold',type=float,default=.9)
    p.add_argument('--drift-threshold',type=float,default=1e-5)
    p.add_argument('--stall-patience',type=int,default=2)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if not all(0<a<=1 for a in args.scales):raise ValueError('FRONTIER_BAD_CORRECTION_SCALES')
    if args.check_every<1 or args.replay_limit<1 or args.quick_guards<1:raise ValueError('FRONTIER_INVALID_SEARCH_CONTROLS')
    return preview(args) if args.stage=='preview' else run(args)

if __name__=='__main__':main()
