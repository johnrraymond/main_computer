#!/usr/bin/env python3
"""Read-only HDB/T diagnostic attribution from already scored heldout cases.

This module NEVER calls model inference and its evaluation examples must NEVER
enter an optimizer batch. It is a report/attribution layer, not a repair bank.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_radius_ladder as ladder

SCHEMA = 'nanojev.qwen.hdb-difficulty.v1'
EVALUATION_ONLY = 'HELDOUT_EVALUATION_ONLY_DO_NOT_TRAIN_ON_REPORTED_CASES'
SIDES = ('negative', 'zero', 'positive')


def _margin(offset):
    k=abs(int(offset))
    if k==0:return 'exact_zero'
    if k<=2:return 'nearest_1_2'
    if k<=8:return 'near_3_8'
    if k<=32:return 'outer_9_32'
    return 'outer_33_plus'


def _failure_type(expected, emitted, *, scoring=None):
    """Distinguish numerical balance failures from historical choice checkpoints."""
    if expected not in ('HAWK','DOVE','TIE'):
        scoring=scoring or {}
        if scoring.get('generation_truncated'):return 'generation_truncated'
        if scoring.get('premature_eos'):return 'premature_eos'
        observed=ladder.parse_packet(emitted)
        if observed is None:
            if not emitted.strip():return 'empty_output'
            return 'invalid_numeric_packet'
        if observed != expected:
            return ('wrong_numeric_balance' if ladder.numeric_terminal(expected) is not None
                    else 'wrong_numeric_reduction')
        return 'grading_inconsistency'
    parsed=competency._grade_choice(emitted)
    if parsed is not None:
        return 'wrong_strategic_choice' if parsed != expected else 'grading_inconsistency'
    raw=emitted.strip()
    if not raw:return 'empty_output'
    if raw.upper().startswith(('PROBLEM:', 'ANSWER:')):return 'arithmetic_packet_instead_of_choice'
    return 'invalid_choice_format'


def _empty():
    return {'tested':0, 'correct':0, 'wrong':0, 'gained':0, 'lost':0,
            'failures_by_type':{}, 'choice_confusion':{}}


def _update(bucket,row,now,before):
    bucket['tested']+=1
    good=bool(now['correct'])
    was=bool(before['correct'])
    bucket['correct']+=int(good)
    bucket['wrong']+=int(not good)
    bucket['gained']+=int(good and not was)
    bucket['lost']+=int(was and not good)
    if not good:
        kind=_failure_type(row['target'],now.get('emitted',''),scoring=now)
        errors=bucket['failures_by_type']
        errors[kind]=errors.get(kind,0)+1
        parsed=(competency._grade_choice(now.get('emitted',''))
                if row['target'] in ('HAWK','DOVE','TIE')
                else ladder.parse_packet(now.get('emitted',''))) or 'INVALID'
        label=f"{row['target']} -> {parsed}"
        bucket['choice_confusion'][label]=bucket['choice_confusion'].get(label,0)+1


def _finish(buckets):
    for bucket in buckets.values():
        bucket['accuracy']=bucket['correct']/bucket['tested'] if bucket['tested'] else None
        bucket['net']=bucket['gained']-bucket['lost']
    return buckets


def analyze_hdb(rows, baseline, current, *, interval=None, exposures=None, rates=None):
    """Separate sign, offset, matrix and tier effects on the same heldout deck."""
    by_tier={f'HDB{i:02d}':_empty() for i in range(9)}
    by_side={side:_empty() for side in SIDES}
    by_margin={}
    by_matrix={}
    by_tier_side={}
    by_tier_margin={}
    errors=[]
    for row in rows:
        key=competency._digest(row['source'])
        now=current[key];before=baseline[key]
        tier=row['tier'];side=row['zero_side'];margin=_margin(row['offset'])
        p=row['payoffs']; matrix=','.join(f'{k}={p[k]}' for k in sorted(p))
        for bucket in (by_tier[tier],by_side[side],
                       by_margin.setdefault(margin,_empty()),
                       by_matrix.setdefault(matrix,_empty()),
                       by_tier_side.setdefault(tier,{}).setdefault(side,_empty()),
                       by_tier_margin.setdefault(tier,{}).setdefault(margin,_empty())):
            _update(bucket,row,now,before)
        if not now['correct']:
            errors.append({'tier':tier,'side':side,'offset':row['offset'],
               'margin_band':margin,'delta':row['delta'],
               'matrix':matrix,'hawk_encounters':row['a'],
               'dove_encounters':row['b'],'hawk_total':row.get('x'),
               'dove_total':row.get('y'),
               'negative_contribution':row.get('negative_contribution'),
               'positive_contribution':row.get('positive_contribution'),
               'correct_balance_half_units':row.get('delta'),
               'target_kind':'terminal_balance' if ladder.numeric_terminal(row['target']) is not None else 'T_one_step_reduction',
               'expected':row['target'],
               'emitted':now.get('emitted',''),'failure_type':_failure_type(row['target'],now.get('emitted',''),scoring=now),
               'finish_reason':now.get('finish_reason','unknown'),
               'generated_tokens':now.get('generated_tokens'),
               'output_budget':now.get('output_budget'),
               'expected_target_tokens':now.get('expected_target_tokens'),
               'exact_prefix':now.get('exact_prefix',False),
               'baseline_correct':bool(before['correct']),
               'source':row['source'], 'evaluation_only':True})
    _finish(by_tier);_finish(by_side);_finish(by_margin);_finish(by_matrix)
    for group in (by_tier_side,by_tier_margin):
        for sub in group.values():_finish(sub)
    interval=interval or {}
    exposures=exposures or {}
    rates=rates or {}
    priority=[]
    for tier,score in by_tier.items():
        interval_net=interval.get(tier,{}).get('net')
        training=exposures.get(tier,{})
        priority.append({'tier':tier,'wrong':score['wrong'],
            'tested':score['tested'],'accuracy':score['accuracy'],
            'net_since_start':score['net'], 'net_since_last_check':interval_net,
            'direct_examples':training.get('examples',0),
            'net_per_100_direct_examples':rates.get(tier,{}).get('net_per_100_direct_examples'),
            'net_per_training_hour':rates.get(tier,{}).get('net_per_training_hour'),
            'net_per_measured_end_to_end_hour':rates.get(tier,{}).get('net_per_measured_end_to_end_hour'),
            'weakest_side':(sorted(by_tier_side.get(tier,{}).items(),
                key=lambda item:(-item[1]['wrong'],item[0]))[0][0]
                if by_tier_side.get(tier) else None),
            'priority_reason':('unmeasured' if not score['tested'] else
                'currently_correct_on_deck' if not score['wrong'] else
                'regressing' if interval_net is not None and interval_net<0 else
                'not_improving_this_interval' if interval_net==0 else
                'improving_but_incomplete' if interval_net is not None and interval_net>0 else
                'not_yet_scored_interval')})
    priority.sort(key=lambda x:(x['tested']==0,-x['wrong'], x['net_since_last_check'] if x['net_since_last_check'] is not None else 0,x['tier']))
    # Concentrations at tier/side; all counts supported by frozen evaluation rows.
    weak_slices=[{'tier':tier,'side':side,'wrong':stats['wrong'],
                  'tested':stats['tested'],'accuracy':stats['accuracy']}
                 for tier,parts in by_tier_side.items() for side,stats in parts.items()
                 if stats['wrong']]
    weak_slices.sort(key=lambda x:(-x['wrong']/x['tested'], -x['wrong'],x['tier'],x['side']))
    # Examples are non-training evidence. Sort to show the nearest-zero errors first.
    errors.sort(key=lambda x:(abs(x['offset']),x['tier'],x['side'],x['matrix']))
    failure_types=dict(sorted(Counter(e['failure_type'] for e in errors).items()))
    return {'failure_types':failure_types,'by_tier':by_tier,'by_side':by_side,'by_margin':by_margin,
            'by_matrix':by_matrix,'by_tier_side':by_tier_side,
            'by_tier_margin':by_tier_margin,
            'ranked_tiers':priority,'ranked_tier_side_weaknesses':weak_slices,
            'wrong_cases':len(errors),'example_failures':errors[:18]},errors


def analyze_t(rows, baseline, current):
    """Observe actual arithmetic replay retention by rung and failure type."""
    by_rung={}
    errors=[]
    for row in rows:
        key=frontier._key(row);now=current[key];before=baseline[key]
        rung=f"T{row['rung']:02d}"
        bucket=by_rung.setdefault(rung,{'tested':0,'correct':0,'wrong':0,
                                         'gained':0,'lost':0,'invalid_packet':0,'wrong_reduction':0})
        good=bool(now['correct']);was=bool(before['correct'])
        bucket['tested']+=1;bucket['correct']+=int(good);bucket['wrong']+=int(not good)
        bucket['gained']+=int(good and not was);bucket['lost']+=int(was and not good)
        if not good:
            kind='invalid_packet' if ladder.parse_packet(now.get('emitted','')) is None else 'wrong_reduction'
            bucket[kind]+=1
            errors.append({'rung':rung,'source':row['source'],'expected':row['target'],
                'emitted':now.get('emitted',''),'failure_type':kind,
                'regression':was,'evaluation_only':True})
    for bucket in by_rung.values():
        bucket['accuracy']=bucket['correct']/bucket['tested'] if bucket['tested'] else None
        bucket['net']=bucket['gained']-bucket['lost']
    ranking=sorted(({'rung':k,**v} for k,v in by_rung.items()),
                   key=lambda item:(-item['lost'],-item['wrong'],item['rung']))
    return {'by_rung':by_rung,'ranked_retention_risks':ranking,
            'regressions':sum(b['lost'] for b in by_rung.values()),
            'wrong_cases':len(errors),'example_failures':errors[:12]},errors


def summarize(rows,trows,baseline_hdb,current_hdb,baseline_t,current_t,
              *,interval=None,exposures=None,rates=None,step=None):
    h,he=analyze_hdb(rows,baseline_hdb,current_hdb,interval=interval,exposures=exposures,rates=rates)
    t,te=analyze_t(trows,baseline_t,current_t)
    report={'schema':SCHEMA,'step':step,'scope':EVALUATION_ONLY,
            'hdb':h,'t_retention':t,'no_new_model_inference':True,
            'limitations':{
                'training_leakage':'Never add these heldout cases to repair or training batches',
                'small_slices':'Nine heldout offsets per payoff matrix and tier; do not infer a population-wide plateau from one interval',
                'causation':'Output categories describe observed behavior, not the hidden mechanism causing it',
                'objective':'HDB00-06 score exact T reduction; HDB07-08 score exact signed terminal balance in half-point units'}}
    return report,he,te


def write_report(out,report,hdb_errors,t_errors):
    out=Path(out)
    ladder._atomic_json(out/'hdb_difficulty_report.json',report)
    # Evaluation-only trace, overwritten at each checkpoint rather than appended.
    path=out/'hdb_difficulty_failures.jsonl'
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf8') as stream:
        for family,items in (('hdb',hdb_errors),('t_retention',t_errors)):
            for item in items:
                stream.write(json.dumps({'family':family,'step':report['step'],**item},sort_keys=True)+'\n')
    tmp.replace(path)


def quick_summary(report):
    h=report['hdb'];t=report['t_retention']
    return {'step':report['step'],
        'weakest_hdb_tiers':[{k:v for k,v in r.items() if k in
          ('tier','wrong','tested','accuracy','net_since_last_check',
           'weakest_side','priority_reason','direct_examples',
           'net_per_100_direct_examples','net_per_training_hour',
           'net_per_measured_end_to_end_hour')}
          for r in h['ranked_tiers'][:5]],
        'zero_line_accuracy':{k:{'correct':v['correct'],'tested':v['tested']}
            for k,v in h['by_side'].items()},
        'nearest_zero_accuracy':{k:{'correct':v['correct'],'tested':v['tested']}
            for k,v in h['by_margin'].items() if k in ('exact_zero','nearest_1_2')},
        'worst_tier_side_slices':h['ranked_tier_side_weaknesses'][:5],
        'hdb_failure_types':h['failure_types'],
        't_retention_regressions':t['regressions'],
        'weakest_t_rungs':[{'rung':v['rung'],'wrong':v['wrong'],'lost':v['lost'],
                            'tested':v['tested']} for v in t['ranked_retention_risks'][:4]],
        'report': 'hdb_difficulty_report.json',
        'examples': 'hdb_difficulty_failures.jsonl'}


def read_latest(args):
    """Report progress from the saved JSON snapshots, without loading Qwen.

    Can be invoked while a training process runs. If a checkpoint transition
    has not finished writing, use the last fully matched report on retry.
    """
    from tools import nanojev_qwen_hdb_frontier as hdb
    out=hdb._output(args)
    progress_file=out/'progress.json';report_file=out/'hdb_report.json'
    if not progress_file.is_file() or not report_file.is_file():
        raise RuntimeError('HDB_DIFFICULTY_REQUIRES_FIRST_COMPLETED_HDB_CHECK')
    state=json.loads(progress_file.read_text(encoding='utf8'))
    report=json.loads(report_file.read_text(encoding='utf8'))
    if int(report['step'])!=int(state['last_step']):
        raise RuntimeError('HDB_DIFFICULTY_CHECKPOINT_WRITE_IN_PROGRESS_RETRY')
    rows=json.loads((out/'hdb_eval_deck.json').read_text(encoding='utf8'))
    trows=json.loads((out/'t_retention_eval_deck.json').read_text(encoding='utf8'))
    data,he,te=summarize(rows,trows,state['baseline_hdb'],state['last_hdb'],
          state['baseline_t'],state['last_t'],
          interval=report['hdb_since_last']['by_tier'],
          exposures=state['exposures'],
          rates=report.get('hdb_velocity_by_tier',{}),step=report['step'])
    write_report(out,data,he,te)
    return data
