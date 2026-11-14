#!/usr/bin/env python3
"""HDB00-HDB08: exact numeric Hawk/Dove balance, shared with T reduction frontier.

This is NOT strategy classification.  Each row is a signed arithmetic task:
HDB00-06 use the existing one-step T packet grammar, and HDB07-08 produce
an exact integer residual in half-point units from explicit math / narrative.
Training/evaluation matrices are disjoint. The classifier checkpoint is never
resumed: the joint trainer uses a separately versioned workspace.
"""
from __future__ import annotations

import random
from decimal import Decimal

from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_hdb_frontier as legacy

SCHEMA='nanojev.qwen.hdb-numeric-balance.v2'
TOTAL=1_000_000
TRAIN_COMMON=legacy.TRAIN_COMMON
TRAIN_SLOPES=legacy.TRAIN_SLOPES
EVAL_VARIANTS=legacy.EVAL_VARIANTS
_checkpoint=legacy._checkpoint
_model=legacy._model
_offset_limit=legacy._offset_limit
_exposures=legacy._exposures
_metrics=legacy._metrics


def _policy(args):
    policy=dict(legacy._policy(args))
    policy.update(schema=SCHEMA, objective='numeric_balance_exact_half_point_units',
                  # Room for long signed numeric packets; never classify HAWK/DOVE.
                  max_new_tokens=max(48, policy['max_new_tokens']),
                  training_target_contract='HDB00-06:T-reduction;HDB07-08:terminal')
    return policy


def _split(n, count):
    """Exact integer partition; preserves the sign, even with arbitrary counts."""
    if count<1:raise ValueError('HDB_PARTITION_INVALID')
    sign=-1 if n<0 else 1
    base,extra=divmod(abs(n),count)
    return [sign*(base+(1 if i<extra else 0)) for i in range(count)]


def _numbers(tier, negative, positive):
    # Reuse canonical T ladder lengths, NOT a separate HDB arithmetic DSL.
    if tier==0:return [negative,positive]
    if tier==1:return [*_split(negative,2),positive]
    if tier==2:return [negative,*_split(positive,3)]
    n2=_split(negative,2);p3=_split(positive,3)
    if tier==3:return [*n2,*p3]
    if tier==4:return [p3[0],n2[0],p3[1],n2[1],p3[2]]
    if tier==5:return [*_split(negative,3),*_split(positive,4)]
    if tier==6:
        neg=_split(negative,4);pos=_split(positive,5)
        return [v for i in range(4) for v in (neg[i],pos[i])]+[pos[4]]
    raise ValueError('HDB_EXPLICIT_MATH_NOT_T_NUMERIC_REDUCTION')


def _display_half(value):
    """Represent a payoff expressed in half-point units as exact plain decimal."""
    v=Decimal(value)/Decimal(2)
    return format(v,'f')


def _row(tier,k,variant,*,label):
    common,p,q=(int(v) for v in variant)
    if common<1 or p<1 or q<1 or not 0<=tier<=8:
        raise ValueError('HDB_BAD_COEFFICIENT_OR_TIER')
    hawks=legacy._pivot(p,q)+int(k)
    doves=TOTAL-hawks
    if not 0<=hawks<=TOTAL:raise ValueError('HDB_OFFSET_OUTSIDE_ENCOUNTER_RANGE')
    # Signed contributions in half-point units; same integer operation as T.
    hawk_coefficient=-common*p
    dove_coefficient=common*q
    negative=hawk_coefficient*hawks
    positive=dove_coefficient*doves
    delta=negative+positive
    assert delta==-common*(p+q)*int(k)
    tier_label=f'HDB{tier:02d}'
    if tier<=6:
        nums=_numbers(tier,negative,positive)
        rung=len(nums)-2
        t=ladder._case(rung,nums,f'{label}-{tier_label}-{common}-{p}-{q}-{k}','numeric')
        source,target=t['source'],t['target']
        chain=t['complete_chain']
    elif tier==7:
        source=(f'Balanced Hawks and Doves arithmetic.\n'
                f'Hawk encounters: {hawks}; Dove encounters: {doves}.\n'
                f'Each Hawk encounter contributes {hawk_coefficient} half-point units.\n'
                f'Each Dove encounter contributes +{dove_coefficient} half-point units.\n'
                f'PROBLEM: ({hawk_coefficient}*{hawks})+({dove_coefficient}*{doves})\n'
                'Compute the exact signed balance in half-point units.\n'
                'Return one canonical terminal PROBLEM: integer packet.')
        target=f'PROBLEM: {delta}';chain=None;rung=None
    else:
        # The original example (-1,3,0,1.5) maps exactly to (-2,+3)
        # in half-point units. Vary payoffs, counts, and true crossing.
        source=(f'In {TOTAL} encounters of Balanced Hawks and Doves, '
                f'the opponent plays Hawk {hawks} times and Dove {doves} times.\n'
                f'Playing Hawk against Hawk pays {_display_half(hawk_coefficient)} points.\n'
                f'Playing Hawk against Dove pays {_display_half(dove_coefficient*2)} points.\n'
                'Playing Dove against Hawk pays 0 points.\n'
                f'Playing Dove against Dove pays {_display_half(dove_coefficient)} points.\n'
                'Calculate the exact signed difference of the cumulative '
                'payoffs from playing Hawk versus playing Dove, '
                'in half-point units.\n'
                'Return one canonical terminal PROBLEM: integer packet.')
        target=f'PROBLEM: {delta}';chain=None;rung=None
    # Source identity excludes label/seed so heldout exclusion catches duplicates.
    return {'id':f'{label}-{tier_label}-P{common}_{p}_{q}-K{k}',
            'tier':tier_label,'family':'hdb','source':source,'target':target,
            'expected':target,'delta':delta,'offset':int(k),'a':hawks,'b':doves,
            'hawk_coefficient':hawk_coefficient,'dove_coefficient':dove_coefficient,
            'negative_contribution':negative,'positive_contribution':positive,
            'rung':rung,'complete_chain':chain,'half_point_units':True,
            'payoffs':{'hawk_hawk':_display_half(hawk_coefficient),
                       'hawk_dove':_display_half(dove_coefficient*2),
                       'dove_hawk':'0','dove_dove':_display_half(dove_coefficient)},
            'zero_side':'positive' if delta>0 else 'negative' if delta<0 else 'zero'}


def _batch(args,step,policy,t_replay,*,tier_override=None):
    rng=random.Random(args.seed ^ (step*2038074743))
    tier=step%9 if tier_override is None else int(tier_override)
    matrix=rng.choice([(c,p,q) for c in TRAIN_COMMON for p,q in TRAIN_SLOPES
                       if not (c==1 and p==q==1)])
    margin=_offset_limit(step,policy['steps'],policy['max_offset'])
    k=rng.randint(1,margin)
    result=[_row(tier,offset,matrix,label=f'HDB-BAL-TRAIN-S{step}')
            for offset in (-k,0,k)]
    count=policy['batch_size']-policy['t_replay_per_batch']
    while len(result)<count:
        t=(tier+len(result))%9
        variant=rng.choice([(c,p,q) for c in TRAIN_COMMON for p,q in TRAIN_SLOPES])
        offset=rng.choice((-1,0,1))*rng.randint(1,margin)
        result.append(_row(t,offset,variant,label=f'HDB-BAL-EXTRA-S{step}'))
    if policy['t_replay_per_batch']:
        result.extend(rng.choice(t_replay) for _ in range(policy['t_replay_per_batch']))
    return result


def _evaluation(policy):
    offsets=competency._hdb_offsets(policy['hdb_eval_cases'])
    result=[_row(t,k,v,label='HDB-BAL-HELDOUT')
            for t in range(9) for v in EVAL_VARIANTS for k in offsets]
    if len({r['source'] for r in result})!=len(result):
        raise RuntimeError('HDB_BALANCE_DUPLICATE_EVAL_PROBLEMS')
    return result


def _expected_output_tokens(tok, target):
    return len(tok(target+'\n',add_special_tokens=False)['input_ids'])


def _inference_budget(tok, row, policy, *, headroom=16):
    """At least enough tokens to emit the target plus a termination margin.

    The frozen policy's 48-token floor remains a minimum, not a hard ceiling.
    This changes diagnostic inference only, never model/optimizer geometry.
    """
    if tok is None:  # Allows existing mock-greedy CPU tests.
        return policy['max_new_tokens'], None
    need=_expected_output_tokens(tok,row['target'])
    budget=max(policy['max_new_tokens'],need+headroom)
    source_len=len(tok(row['source']+'\n',add_special_tokens=False)['input_ids'])
    if source_len+budget>policy['max_tokens']:
        raise ValueError(f'HDB_DIAGNOSTIC_CONTEXT_TOO_SHORT:{row["tier"]}:needed={source_len+budget}:limit={policy["max_tokens"]}')
    return budget,need


def _score_hdb(model,tok,rows,args,policy,*,greedy=None):
    """Length-safe exact heldout scores, with authentic decoder finish reasons."""
    cb=greedy if greedy is not None else competency._diagnostic_greedy(args,return_metadata=True)
    scores={}
    # Group by needed answer budget so easy cases do not pay for long packets.
    by_budget={}
    for row in rows:
        budget,need=_inference_budget(tok,row,policy)
        by_budget.setdefault(budget,[]).append((row,need))
    done=0
    for budget,group in sorted(by_budget.items()):
        for i in range(0,len(group),args.eval_batch):
            chunk=group[i:i+args.eval_batch]
            predictions=cb(model,tok,[r['source'] for r,_ in chunk],args.device,
                           policy['max_tokens'],budget)
            for (row,need),prediction in zip(chunk,predictions):
                details=prediction if isinstance(prediction,dict) else {'text':prediction}
                raw=details['text']
                emitted=ladder.parse_packet(raw)
                finish=details.get('finish_reason','unknown')
                prefix=len(raw)<len(row['target']) and row['target'].startswith(raw)
                scores[competency._digest(row['source'])]={
                    'correct':emitted==row['target'],'emitted':raw,'parsed':emitted,
                    'numerical_terminal':ladder.numeric_terminal(raw),
                    'finish_reason':finish,'generated_tokens':details.get('generated_tokens'),
                    'output_budget':budget,'expected_target_tokens':need,
                    'exact_prefix':prefix,
                    'generation_truncated':finish=='length',
                    'premature_eos':finish=='eos' and prefix}
            done+=len(chunk)
            if ((done-1)//max(1,args.eval_batch))%max(1,getattr(args,'competency_progress_every',1))==0:
                frontier._emit('QWEN_HDB_PROGRESS',{'done':done,'total':len(rows)})
    return scores


def _chain_deck(rows):
    """Small frozen end-to-end sample spanning every tier and zero-line side.

    Same heldout identities as the main HDB diagnostic (no training leakage).
    Use one unseen payoff family and nearest -1/0/+1 count offsets per tier.
    """
    variant=EVAL_VARIANTS[0]
    matrix={
        'hawk_coefficient':-variant[0]*variant[1],
        'dove_coefficient':variant[0]*variant[2],
    }
    chosen=[r for r in rows if r['hawk_coefficient']==matrix['hawk_coefficient']
            and r['dove_coefficient']==matrix['dove_coefficient']
            and r['offset'] in (-1,0,1)]
    if len(chosen)!=27 or len({r['tier'] for r in chosen})!=9:
        raise RuntimeError('HDB_BALANCE_CHAIN_DECK_INCOMPLETE')
    return chosen


def _score_balance_chains(model,tok,rows,args,policy,*,greedy=None):
    """Autonomous numerical completion; never injects a correct intermediate.

    For HDB00-06 the model receives the same exact numeric T packet at each hop.
    For HDB07-08 it must emit the final terminal in one model generation.
    """
    cb=greedy if greedy is not None else competency._diagnostic_greedy(args,return_metadata=True)
    result={}
    for i,row in enumerate(rows):
        source=row['source']
        expected_steps=(row['complete_chain'][1:] if row['complete_chain'] is not None
                        else [row['target']])
        first_wrong=None
        trace=[]
        terminal=None
        for j,expected in enumerate(expected_steps):
            budget,_=_inference_budget(tok,{'source':source,'target':expected,'tier':row['tier']},policy)
            generation=cb(model,tok,[source],args.device,policy['max_tokens'],budget)[0]
            emission=generation['text'] if isinstance(generation,dict) else generation
            finish=generation.get('finish_reason','unknown') if isinstance(generation,dict) else 'unknown'
            parsed=ladder.parse_packet(emission)
            if first_wrong is None and parsed!=expected:
                first_wrong=f'T{len(expected_steps)-j-1:02d}' if row['rung'] is not None else row['tier']
            trace.append({'input':source,'expected':expected,'emitted':emission,
                          'finish_reason':finish,'output_budget':budget,
                          'exact':parsed==expected})
            if parsed is None or parsed==source:
                break
            terminal=ladder.numeric_terminal(parsed)
            if terminal is not None:
                break
            source=parsed
        strict=(first_wrong is None and len(trace)==len(expected_steps)
                and terminal==row['delta'])
        result[competency._digest(row['source'])]={
            'correct':strict,'terminal_correct':terminal==row['delta'],
            'terminal':terminal,'completed_hops':len(trace),'expected_hops':len(expected_steps),
            'first_wrong_rung':first_wrong,'trace':trace}
        frontier._emit('QWEN_HDB_BALANCE_CHAIN_PROGRESS',{'done':i+1,'total':len(rows),
                         'strict_correct':sum(v['correct'] for v in result.values())})
    return result


def _chain_metrics(rows,baseline,current):
    result={'tested':0,'correct':0,'terminal_correct':0,'gained':0,'lost':0,
            'by_tier':{f'HDB{i:02d}':{'tested':0,'correct':0,'gained':0,'lost':0}
                       for i in range(9)},'first_failure_by_tier':{}}
    for row in rows:
        key=competency._digest(row['source'])
        before=bool(baseline[key]['correct']);now=current[key]
        okay=bool(now['correct']);bucket=result['by_tier'][row['tier']]
        for group in (result,bucket):
            group['tested']+=1;group['correct']+=int(okay)
            group['gained']+=int(okay and not before)
            group['lost']+=int(before and not okay)
        result['terminal_correct']+=int(now['terminal_correct'])
        if not okay:
            label=now['first_wrong_rung'] or 'incomplete_or_invalid'
            result['first_failure_by_tier'][label]=result['first_failure_by_tier'].get(label,0)+1
    result['net']=result['gained']-result['lost']
    result['accuracy']=result['correct']/result['tested'] if result['tested'] else None
    for item in result['by_tier'].values():
        item['accuracy']=item['correct']/item['tested'] if item['tested'] else None
        item['net']=item['gained']-item['lost']
    return result
