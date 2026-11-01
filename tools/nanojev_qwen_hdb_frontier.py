#!/usr/bin/env python3
"""Opt-in zero-line Balanced Hawks and Doves training after Qwen T frontier.

HDB00-HDB08 are semantic strategy tiers, NOT arithmetic reduction T rungs.
Trains q/v LoRA with explicit payoff bindings, exact delta ties, both sides
of the zero crossing; evaluates reserved payoff matrices and T retention.
Isolated HDB checkpoint and policy, never mutates the completed T checkpoint.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import random
import time

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_hdb_difficulty as difficulty

SCHEMA = 'nanojev.qwen.hdb-zero-line-frontier.v1'
TOTAL = 1_000_000
# Opponent indifference point: a0 = 1_000_000*q/(p+q).
# Avoid the original HDB diagnostic matrix (common=1, p=q=1), and keep
# training/evaluation PAYOFF MATRIX families mutually exclusive.
TRAIN_COMMON = (1, 2, 3, 4)
TRAIN_SLOPES = ((1, 1), (2, 3), (3, 2), (1, 3), (3, 1), (2, 2))
EVAL_VARIANTS = ((5, 2, 3), (7, 3, 2), (9, 1, 3))


def _output(args):
    base = frontier._out(args)
    return Path(args.hdb_output) if getattr(args, 'hdb_output', None) else base.with_name(base.name + '_hdb_frontier_v1')


def _parent(args):
    return frontier._out(args) / 'working.pt'


def _payoffs(common, p, q):
    return dict(zip(hd.TAGS, map(Decimal,(common+p,common,common,common+q))))


def _pivot(p, q):
    numerator = TOTAL * q
    if numerator % (p+q):
        raise ValueError('HDB_UNREPRESENTABLE_EXACT_TIE')
    return numerator // (p+q)


def _row(tier, k, variant, *, label):
    common,p,q = variant
    a = _pivot(p,q)+k
    b = TOTAL-a
    if not (0 <= a <= TOTAL):
        raise ValueError('HDB_OFFSET_OUTSIDE_ENCOUNTER_RANGE')
    payoff=_payoffs(common,p,q)
    x,y,decision=hd.oracle(a,b,payoff)
    delta=int(x-y)
    assert delta == (p+q)*k
    presentation=hd.render(f'T{tier:02d}',a,b,payoff,full_slots=())
    prompt=competency._hdb_prompt(presentation,payoff)
    return {'id':f'{label}-HDB{tier:02d}-P{common}_{p}_{q}-K{k}',
            'tier':f'HDB{tier:02d}', 'source':prompt,'target':decision,
            'expected':decision,'delta':delta, 'offset':k,'x':str(x),'y':str(y),
            'a':a,'b':b,'payoffs':{tag:str(value) for tag,value in payoff.items()},
            'zero_side':'positive' if delta>0 else 'negative' if delta<0 else 'zero',
            'family':'hdb'}


def _policy(args):
    steps=int(getattr(args,'hdb_steps',900))
    check=int(getattr(args,'hdb_check_every',100))
    offset=int(getattr(args,'hdb_max_offset',64))
    replay=int(getattr(args,'hdb_t_replay_per_batch',1))
    length=int(getattr(args,'hdb_max_seq',768))
    tokens=int(getattr(args,'hdb_max_tokens',1024))
    new=int(getattr(args,'hdb_max_new_tokens',12))
    if (steps<1 or check<1 or not (1<=offset<=50000) or replay<0 or
        args.batch_size-replay<3 or length<256 or length>4096 or
        tokens<=new or tokens>4096 or new<3 or new>64 or
        args.terminal_mode!='numeric' or args.max_rung!=8):
        raise ValueError('HDB_INVALID_TRAINING_POLICY')
    return {'schema':SCHEMA,'steps':steps,'check_every':check,'max_offset':offset,
            't_replay_per_batch':replay,'max_seq':length,'max_tokens':tokens,
            'max_new_tokens':new,'batch_size':args.batch_size,'seed':args.seed,
            'lr':args.lr,'source_frontier':str(frontier._out(args).resolve()),
            'eval_variants':EVAL_VARIANTS,'train_common':TRAIN_COMMON,
            'train_slopes':TRAIN_SLOPES,
            'hdb_eval_cases':int(getattr(args,'competency_hdb_cases',9))}


def _offset_limit(step, steps, maximum):
    # Start at the finest possible zero-line difference, widen only after
    # acquiring training exposure at narrower margins; always sample k=0.
    stage=min(7,step*8//steps)
    return min(maximum,2**stage)


def _batch(args, step, policy, t_replay, *, tier_override=None):
    rng=random.Random(args.seed ^ (step*2038074743))
    t=step%9 if tier_override is None else int(tier_override)
    common,p,q=rng.choice([(c,a,b) for c in TRAIN_COMMON
                           for a,b in TRAIN_SLOPES
                           if not (c==1 and a==b==1)])
    margin=_offset_limit(step,policy['steps'],policy['max_offset'])
    # Guarantee every optimizer step contains an exact tie and BOTH sides.
    k=rng.choice([1,min(2,margin),rng.randint(1,margin)])
    k=max(1,min(margin,k))
    variant=(common,p,q)
    result=[_row(t,kk,variant,label=f'HDB-TRAIN-S{step}') for kk in (-k,0,k)]
    count=policy['batch_size']-policy['t_replay_per_batch']
    while len(result)<count:
        t2=(t+len(result))%9
        common,p,q=rng.choice([(c,a,b) for c in TRAIN_COMMON for a,b in TRAIN_SLOPES
                                if not (c==1 and a==b==1)])
        kk=rng.choice((-1,0,1))*rng.randint(1,margin)
        result.append(_row(t2,kk,(common,p,q),label=f'HDB-EXTRA-S{step}-{len(result)}'))
    if policy['t_replay_per_batch']:
        result.extend(rng.choice(t_replay) for _ in range(policy['t_replay_per_batch']))
    return result


def _evaluation(policy):
    # Immutable HDB decision/near-zero deck. Distinct matrices, counts and
    # prompts from the actual HDB training family.
    offsets=competency._hdb_offsets(policy['hdb_eval_cases'])
    rows=[]
    for tier in range(9):
        for variant in EVAL_VARIANTS:
            for k in offsets:
                rows.append(_row(tier,k,variant,label='HDB-HELDOUT'))
    assert len({r['source'] for r in rows})==len(rows)
    return rows


def _replay(args):
    # Reuse historical arithmetic training families, never competency heldouts.
    rows=[]
    radii=frontier._radii(args)
    for j,r in enumerate(radii):
        low=0 if j==0 else radii[j-1]+1
        rows.extend(frontier._deck(r,low,args.frontier_train_cases,args,
                                   f'TRAIN-{j}-C{j*args.frontier_steps//len(radii)//args.frontier_check_every}'))
    return rows


def _t_eval(args):
    # The original frontier evaluation was excluded from arithmetic training.
    rows=frontier._evaluation_deck(args,frontier._radii(args))
    stride=max(1,len(rows)//45)
    return rows[::stride]


def _score_hdb(model,tok,rows,args,policy,*,greedy=None):
    cb=greedy or competency._diagnostic_greedy(args)
    result={}
    for i in range(0,len(rows),args.eval_batch):
        chunk=rows[i:i+args.eval_batch]
        emitted=cb(model,tok,[r['source'] for r in chunk],args.device,
                   policy['max_tokens'],policy['max_new_tokens'])
        for row,answer in zip(chunk,emitted):
            result[competency._digest(row['source'])]={
                'correct':competency._grade_choice(answer)==row['target'],
                'emitted':answer}
        if (i//args.eval_batch)%max(1,getattr(args,'competency_progress_every',1))==0:
            frontier._emit('QWEN_HDB_PROGRESS',{'done':min(len(rows),i+len(chunk)),
                                               'total':len(rows)})
    return result


def _metrics(rows,baseline,current):
    # Group by HDB tier and sign, paired to the same fixed cases.
    def blank():return {'tested':0,'correct':0,'gained':0,'lost':0,'net':0,
                         'by_side':{side:{'tested':0,'correct':0} for side in ('negative','zero','positive')}}
    overall=blank();tiers={f'HDB{i:02d}':blank() for i in range(9)}
    for row in rows:
        key=competency._digest(row['source'])
        before=baseline[key]['correct']; after=current[key]['correct']
        for target in (overall,tiers[row['tier']]):
            target['tested']+=1;target['correct']+=int(after)
            target['gained']+=int(after and not before)
            target['lost']+=int(before and not after)
            side=target['by_side'][row['zero_side']]
            side['tested']+=1;side['correct']+=int(after)
    for item in (overall,*tiers.values()):
        item['net']=item['gained']-item['lost']
        item['accuracy']=item['correct']/item['tested'] if item['tested'] else None
    return {'total':overall,'by_tier':tiers}


def _t_metrics(rows,baseline,current):
    return frontier._transitions(baseline,current,rows)


def _save(out,state,model,zero,opt):
    ladder._atomic_torch(out/'working.pt',{
        'schema':SCHEMA,'state':state,'lora':zero.lora_state(model),
        'optimizer':opt.state_dict()})
    ladder._atomic_json(out/'progress.json',state)


def _checkpoint(args, policy):
    import torch
    source=_parent(args)
    if not source.is_file():
        raise RuntimeError('HDB_REQUIRES_COMPLETED_ARITHMETIC_FRONTIER_WORKING_PT')
    digest=frontier._sha(source)
    obj=torch.load(source,map_location='cpu',weights_only=True)
    if (obj.get('schema')!=frontier.SCHEMA or
        obj['state']['status']!='STRESSED' or
        obj['state']['phase_step']!=args.frontier_steps or
        obj['state']['policy']!=frontier._policy(args)):
        raise RuntimeError('HDB_PARENT_FRONTIER_NOT_COMPLETE_OR_POLICY_DRIFT')
    return obj,digest


def _model(args,parent):
    # _load_model uses the source numeric ladder checkpoint for architecture.
    # Restore the stressed arithmetic LoRA, NOT the original ladder LoRA.
    source,_=frontier._load_source(args)
    model,tok,zero,ps=frontier._load_model(args,source)
    zero.restore_lora(model,parent['lora'])
    return model,tok,zero,ps


def _assess(args,policy,model,tok,rows,trows,initial_hdb,initial_t,
            *,previous_hdb=None,previous_t=None,trained_steps=0,seconds=0.,exposures=None,previous_exposures=None):
    model.eval()
    hdb=_score_hdb(model,tok,rows,args,policy)
    tscore=frontier._score(model,tok,trows,args,
                          greedy=competency._diagnostic_greedy(args))
    hbase=_metrics(rows,initial_hdb,hdb)
    hpast=_metrics(rows,previous_hdb or initial_hdb,hdb)
    tbase=_t_metrics(trows,initial_t,tscore)
    tpast=_t_metrics(trows,previous_t or initial_t,tscore)
    rate={}
    for tier,score in hpast['by_tier'].items():
        current_exposure=(exposures or {}).get(tier,{}).get('examples',0)
        old_exposure=(previous_exposures or {}).get(tier,{}).get('examples',0)
        exposure_delta=current_exposure-old_exposure
        rate[tier]={
            'net_since_last_check':score['net'],
            'net_per_100_optimizer_steps':100*score['net']/trained_steps if trained_steps else None,
            'net_per_training_hour':3600*score['net']/seconds if seconds>0 else None,
            'net_per_100_direct_examples':100*score['net']/exposure_delta if exposure_delta>0 else None,
            'interval_training_examples':exposure_delta,
            'cumulative_training_examples':current_exposure,
            'cumulative_training_steps':(exposures or {}).get(tier,{}).get('steps',0)}
    return {'hdb_from_start':hbase,'hdb_since_last':hpast,
            't_retention_from_start':tbase,'t_retention_since_last':tpast,
            'hdb_velocity_by_tier':rate},hdb,tscore


def _exposures():
    return {f'HDB{i:02d}':{'examples':0,'steps':0,
            'negative':0,'zero':0,'positive':0} for i in range(9)}


def _add_exposures(state,batch):
    seen=set()
    for row in batch:
        if row.get('family')!='hdb':
            state['t_replay_examples']+=1
            continue
        item=state['exposures'][row['tier']]
        item['examples']+=1
        item[row['zero_side']]+=1
        seen.add(row['tier'])
    for tier in seen:
        state['exposures'][tier]['steps']+=1


def run(args, *, audit_only=False):
    import torch
    policy=_policy(args)
    parent,parent_sha=_checkpoint(args,policy)
    out=_output(args)
    ladder._outputs_safe(out,[frontier._out(args),Path(args.output),
                             Path(args.stage1_checkpoint).parent,
                             Path(args.text_checkpoint).parent])
    out.mkdir(parents=True,exist_ok=True)
    cp=out/'working.pt'
    if not cp.exists() and any(out.iterdir()):
        raise RuntimeError('HDB_REFUSES_UNRECOGNIZED_OUTPUT_WORKSPACE')
    model,tok,zero,ps=_model(args,parent)
    eval_rows=_evaluation(policy)
    trows=_t_eval(args)
    fp=ladder.deck_fingerprint(eval_rows)+':'+ladder.deck_fingerprint(trows)
    if cp.exists():
        obj=torch.load(cp,map_location='cpu',weights_only=True)
        state=obj['state']
        if (obj.get('schema')!=SCHEMA or state['policy']!=policy or
            state['parent_sha256']!=parent_sha or state['eval_sha256']!=fp):
            raise RuntimeError('HDB_RESUME_POLICY_SOURCE_OR_EVAL_DRIFT')
        zero.restore_lora(model,obj['lora'])
        opt=frontier._optimizer(ps,args,obj['optimizer'])
    else:
        if audit_only:raise RuntimeError('HDB_AUDIT_REQUIRES_EXISTING_HDB_WORKING_PT')
        # Original T optimizer history is retained, with independent HDB copy.
        opt=frontier._optimizer(ps,args,parent['optimizer'])
        model.eval()
        frontier._emit('QWEN_HDB_BASELINE_START',{'hdb_cases':len(eval_rows),
                  't_retention_cases':len(trows)})
        baseline_evaluation_start=time.perf_counter()
        initial_hdb=_score_hdb(model,tok,eval_rows,args,policy)
        initial_t=frontier._score(model,tok,trows,args,
                        greedy=competency._diagnostic_greedy(args))
        baseline_seconds=time.perf_counter()-baseline_evaluation_start
        state={'schema':SCHEMA,'policy':policy,'parent_sha256':parent_sha,
               'baseline_evaluation_seconds':baseline_seconds,
               'eval_sha256':fp,'phase_step':0,'training_seconds':0.,
               'status':'TRAINING','baseline_hdb':initial_hdb,
               'baseline_t':initial_t,'last_hdb':initial_hdb,'last_t':initial_t,
               'last_step':0,'last_training_seconds':0.,
               'exposures':_exposures(),'t_replay_examples':0,
               'best_hdb_correct':sum(v['correct'] for v in initial_hdb.values()),
               'best_step':0}
        ladder._atomic_json(out/'hdb_eval_deck.json',eval_rows)
        ladder._atomic_json(out/'t_retention_eval_deck.json',trows)
        _save(out,state,model,zero,opt)
        _save_best(out,state,model,zero,opt)
        frontier._emit('QWEN_HDB_START',{'output':str(out),'steps':policy['steps'],
                'parent_step':parent['state']['phase_step'],
                'zero_line':'delta=X-Y; negative/zero/positive in each batch'})
    if audit_only or state['status']=='COMPLETE':
        report_path=out/'hdb_report.json'
        if report_path.exists():
            report=json.loads(report_path.read_text(encoding='utf8'))
            if report.get('step')==state['phase_step']:
                frontier._emit('QWEN_HDB_REPORT',{'report':str(report_path),'step':report['step'],
                     'hdb':report['hdb_from_start']['total'],
                     't_retention':report['t_retention_from_start']})
                if audit_only:return report
                return state
        if audit_only:
            report,_,_=_assess(args,policy,model,tok,eval_rows,trows,
                          state['baseline_hdb'],state['baseline_t'],
                          exposures=state['exposures'])
            report.update({'step':state['phase_step'],'training_seconds':state['training_seconds']})
            ladder._atomic_json(report_path,report)
            return report
        frontier._emit('QWEN_HDB_DONE',{'already_complete':True,'step':state['phase_step']})
        return state
    replay=_replay(args) if policy['t_replay_per_batch'] else []
    trainargs=copy.copy(args)
    trainargs.max_seq=policy['max_seq']
    for step in range(state['phase_step'],policy['steps']):
        batch=_batch(args,step,policy,replay)
        model.train();start=time.perf_counter()
        loss=ladder._train_step(model,tok,opt,batch,trainargs)
        if str(args.device).startswith('cuda'):torch.cuda.synchronize()
        duration=time.perf_counter()-start
        model.eval()
        state['training_seconds']+=duration
        state['phase_step']=step+1
        _add_exposures(state,batch)
        if (step+1)%policy['check_every']!=0 and step+1!=policy['steps']:
            continue
        interval_steps=state['phase_step']-state['last_step']
        last_exposures=copy.deepcopy(state.get('last_exposures',_exposures()))
        interval_seconds=state['training_seconds']-state['last_training_seconds']
        evaluation_start=time.perf_counter()
        data,hs,ts=_assess(args,policy,model,tok,eval_rows,trows,
            state['baseline_hdb'],state['baseline_t'],
            previous_hdb=state['last_hdb'],previous_t=state['last_t'],
            trained_steps=interval_steps,seconds=interval_seconds,
            exposures=state['exposures'],previous_exposures=last_exposures)
        evaluation_seconds=time.perf_counter()-evaluation_start
        measured_seconds=(interval_seconds+evaluation_seconds+
            (state.get('baseline_evaluation_seconds',0.) if state['last_step']==0 else 0.))
        for rate in data['hdb_velocity_by_tier'].values():
            rate['net_per_measured_end_to_end_hour']=(
                3600*rate['net_since_last_check']/measured_seconds
                if measured_seconds>0 else None)
        # Attribution uses the already-scored heldout cases: zero extra Qwen inference.
        difficult,hdb_errors,t_errors=difficulty.summarize(eval_rows,trows,
            state['baseline_hdb'],hs,state['baseline_t'],ts,
            interval=data['hdb_since_last']['by_tier'],
            exposures=state['exposures'],
            rates=data['hdb_velocity_by_tier'],step=step+1)
        data.update({'schema':SCHEMA,'step':step+1,'training_seconds':state['training_seconds'],
                     'evaluation_seconds':evaluation_seconds,
                     'interval_total_measured_seconds':measured_seconds,
                     'interval_training_seconds':interval_seconds,
                     'interval_steps':interval_steps,'training_exposure_cumulative':state['exposures'],
                     't_replay_examples':state['t_replay_examples'],
                     'parent_sha256':parent_sha,'eval_sha256':fp,
                     'loss':round(loss,8),'max_offset':_offset_limit(step,policy['steps'],policy['max_offset'])})
        state['last_hdb']=hs;state['last_t']=ts
        state['last_exposures']=copy.deepcopy(state['exposures'])
        state['last_step']=state['phase_step']
        state['last_training_seconds']=state['training_seconds']
        if data['hdb_from_start']['total']['correct']>state['best_hdb_correct']:
            state['best_hdb_correct']=data['hdb_from_start']['total']['correct']
            state['best_step']=state['phase_step']
            improved=True
        else:improved=False
        _save(out,state,model,zero,opt)
        if improved:_save_best(out,state,model,zero,opt)
        ladder._atomic_json(out/'hdb_report.json',data)
        difficulty.write_report(out,difficult,hdb_errors,t_errors)
        frontier._append(out/'hdb_history.jsonl',{
            'step':state['phase_step'],'hdb':data['hdb_from_start'],
            'hdb_interval':data['hdb_since_last'],'t_retention':data['t_retention_from_start'],
            'rates':data['hdb_velocity_by_tier'],
            'training_seconds':state['training_seconds'],
            'evaluation_seconds':evaluation_seconds,
            'exposures':state['exposures']})
        frontier._emit('QWEN_HDB_DIFFICULTY',difficulty.quick_summary(difficult))
        frontier._emit('QWEN_HDB_CHECK',{'step':state['phase_step'],
            'hdb':data['hdb_from_start']['total'],
            'by_tier':{k:{'correct':v['correct'],'tested':v['tested'],'net':v['net']}
                       for k,v in data['hdb_from_start']['by_tier'].items()},
            't_retention':data['t_retention_from_start'],
            'training_seconds':round(state['training_seconds'],2),'last_loss':round(loss,8)})
    state['status']='COMPLETE'
    _save(out,state,model,zero,opt)
    frontier._emit('QWEN_HDB_DONE',{'step':state['phase_step'],
            'best_step':state['best_step'],'report':str(out/'hdb_report.json')})
    return state


def _save_best(out,state,model,zero,opt):
    ladder._atomic_torch(out/'best.pt',{'schema':SCHEMA,'state':state,
                      'lora':zero.lora_state(model),'optimizer':opt.state_dict()})
