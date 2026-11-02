#!/usr/bin/env python3
"""Read-only HDB/T diagnostic attribution from already scored heldout cases.

This module NEVER calls model inference and its evaluation examples must NEVER
enter an optimizer batch. It is a report/attribution layer, not a repair bank.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from statistics import median
from pathlib import Path

from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_radius_ladder as ladder

SCHEMA = 'nanojev.qwen.hdb-difficulty.v1'
EVALUATION_ONLY = 'HELDOUT_EVALUATION_ONLY_DO_NOT_TRAIN_ON_REPORTED_CASES'
SIDES = ('negative', 'zero', 'positive')
BALANCE_ERROR_SCHEMA = 'hdb-approximate-balance-error.v1'
BALANCE_TERMINAL_TIERS = ('HDB07', 'HDB08')


def _quantile(values, fraction):
    """Linear interpolation of ordered finite samples, with no dependencies."""
    ordered=sorted(values)
    if not ordered:return None
    at=(len(ordered)-1)*fraction
    lo=int(at);hi=min(lo+1,len(ordered)-1)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(at-lo)


def _sign(value):
    return (value>0)-(value<0)


def _observed_balance(now):
    """Never infer an answer from partial prose or incomplete expressions."""
    value=now.get('numerical_terminal')
    if isinstance(value,int) and not isinstance(value,bool):
        return value
    try:
        return ladder.numeric_terminal(now.get('emitted',''))
    except (ValueError,OverflowError):
        return None


def _balance_error_case(row,now):
    """Residual in *one encounter offset* units, not gross million-event totals."""
    if row.get('tier') not in BALANCE_TERMINAL_TIERS:
        return None
    observed=_observed_balance(now)
    if observed is None:
        return None
    expected=int(row['delta'])
    # A one-encounter displacement exchanges one Hawk for one Dove:
    # delta changes by |hawk_coefficient|+|dove_coefficient| half-points.
    unit=abs(int(row['hawk_coefficient']))+abs(int(row['dove_coefficient']))
    if unit<1:raise ValueError('HDB_ERROR_INVALID_ENCOUNTER_UNIT')
    residual=observed-expected
    scaled=residual/unit
    return {'expected_balance_half_units':expected,
            'observed_balance_half_units':observed,
            'signed_error_half_units':residual,
            'one_encounter_offset_half_units':unit,
            'signed_error_in_offset_units':scaled,
            'log_signed_error_in_offset_units':math.copysign(math.log1p(abs(scaled)),scaled),
            'correct_sign':_sign(observed)==_sign(expected),
            'within_one_offset':abs(residual)<=unit,
            'within_two_offsets':abs(residual)<=2*unit}


def _summarize_balance_cases(cases,tested,exact):
    z=[v['log_signed_error_in_offset_units'] for v in cases]
    absolute=[abs(v['signed_error_in_offset_units']) for v in cases]
    center=median(z) if z else None
    mad=median([abs(v-center) for v in z]) if z else None
    # Bimodal sign flips can produce MAD=0. IQR still exposes dispersion.
    q25=_quantile(z,.25);q75=_quantile(z,.75)
    robust_sigma=(q75-q25)/1.349 if q25 is not None else None
    count=len(cases)
    return {'tested':tested, 'numeric_outputs':count,
            'non_numeric_outputs':tested-count,
            'exact_correct':exact,
            'exact_accuracy':exact/tested if tested else None,
            'median_absolute_error_offset_units':median(absolute) if absolute else None,
            'p90_absolute_error_offset_units':_quantile(absolute,.90),
            'p95_absolute_error_offset_units':_quantile(absolute,.95),
            'max_absolute_error_offset_units':max(absolute) if absolute else None,
            'outputs_over_ten_offsets':sum(value>10 for value in absolute),
            'median_signed_log_offset_error':center,
            'mad_signed_log_offset_error':mad,
            'iqr_signed_log_offset_error':q75-q25 if q25 is not None else None,
            'robust_sigma_signed_log_offset_error':robust_sigma,
            'robust_variance_signed_log_offset_error':robust_sigma**2 if robust_sigma is not None else None,
            'correct_sign':sum(c['correct_sign'] for c in cases),
            'within_one_offset':sum(c['within_one_offset'] for c in cases),
            'within_two_offsets':sum(c['within_two_offsets'] for c in cases),
            'correct_sign_rate_of_numeric':sum(c['correct_sign'] for c in cases)/count if count else None,
            'within_one_offset_rate_of_all':sum(c['within_one_offset'] for c in cases)/tested if tested else None,
            'within_two_offsets_rate_of_all':sum(c['within_two_offsets'] for c in cases)/tested if tested else None}


def balance_error_distribution(rows,current):
    """Robust, scale-aware *observational* error diagnostics for HDB07/08.

    Strict grading is untouched. Residuals are normalized by the exact
    one-encounter payoff difference then transformed using signed log1p.
    IQR-based variance resists extreme scale jumps; MAD is retained as another
    summary, and raw tail quantiles expose catastrophic outliers. Report each
    of the positive/negative/zero sides separately to avoid masking bias.
    """
    buckets={tier:[] for tier in BALANCE_TERMINAL_TIERS}
    tested={tier:0 for tier in BALANCE_TERMINAL_TIERS}
    exact={tier:0 for tier in BALANCE_TERMINAL_TIERS}
    sides={tier:{side:[] for side in SIDES} for tier in BALANCE_TERMINAL_TIERS}
    side_tested={tier:{side:0 for side in SIDES} for tier in BALANCE_TERMINAL_TIERS}
    side_exact={tier:{side:0 for side in SIDES} for tier in BALANCE_TERMINAL_TIERS}
    for row in rows:
        tier=row.get('tier')
        if tier not in buckets:continue
        side=row['zero_side']
        tested[tier]+=1;side_tested[tier][side]+=1
        key=competency._digest(row['source'])
        now=current[key]
        is_exact=int(bool(now.get('correct')))
        exact[tier]+=is_exact;side_exact[tier][side]+=is_exact
        record=_balance_error_case(row,now)
        if record is not None:
            buckets[tier].append(record)
            sides[tier][side].append(record)
    result={}
    for tier,cases in buckets.items():
        stats=_summarize_balance_cases(cases,tested[tier],exact[tier])
        stats['by_side']={side:_summarize_balance_cases(
            sides[tier][side],side_tested[tier][side],side_exact[tier][side])
            for side in SIDES}
        result[tier]=stats
    return {'schema':BALANCE_ERROR_SCHEMA,
        'unit':'one-encounter displacement in half-point balance units',
        'transform':'sign(error)*ln(1+abs(error)/(abs(hawk_coefficient)+abs(dove_coefficient)))',
        'robust_variance_definition':'((Q75-Q25)/1.349)**2 of signed transformed error; log-offset units squared; MAD is separately reported',
        'interpretation':'Median absolute and p90 errors and robust variance should shrink; sign accuracy and tolerance rates should rise; exact grading unchanged',
        'by_tier':result}


def _compare_balance_record(old,now):
    fields=('median_absolute_error_offset_units','p90_absolute_error_offset_units',
            'robust_variance_signed_log_offset_error','correct_sign_rate_of_numeric',
            'within_one_offset_rate_of_all','exact_accuracy')
    if not old or old['tested']!=now['tested']:
        return None
    # A newly parseable/malformed answer changes the measured population.
    # Keep such transitions visible but don't mislabel a selection change as
    # convergence of the same numerical residual population.
    matched_numeric=(old['numeric_outputs']==old['tested'] and
                     now['numeric_outputs']==now['tested'])
    return {'previous_numeric_outputs':old['numeric_outputs'],
            'current_numeric_outputs':now['numeric_outputs'],
            'fully_comparable_numeric_residuals':matched_numeric,
            'deltas':{field:(now[field]-old[field] if
                    matched_numeric and now[field] is not None and old[field] is not None
                    else None) for field in fields},
            'robust_spread_shrinking':(
                now['robust_variance_signed_log_offset_error']<old['robust_variance_signed_log_offset_error']
                if matched_numeric and now['tested'] else None),
            'note':'Negative error/spread deltas are improvements, not proof of causal training gain; missing terminal packets disallow matched residual comparisons'}


def compare_balance_distributions(previous,current):
    """Compare the same fixed-deck measurement under the same scoring schema."""
    if not previous or previous.get('schema')!=current.get('schema'):
        return None
    result={}
    for tier,now in current['by_tier'].items():
        old=previous.get('by_tier',{}).get(tier)
        summary=_compare_balance_record(old,now)
        if summary is None:continue
        summary['by_side']={side:delta for side,current_side in now.get('by_side',{}).items()
            if (delta:=_compare_balance_record(old.get('by_side',{}).get(side),current_side)) is not None}
        result[tier]=summary
    return result


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
               'balance_error':_balance_error_case(row,now),
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
            'wrong_cases':len(errors),'example_failures':errors[:18],
            'balance_error_distribution':balance_error_distribution(rows,current)},errors


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
        'hdb_approximate_balance':h['balance_error_distribution']['by_tier'],
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
