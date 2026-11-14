#!/usr/bin/env python3
"""Read-only matched operand perturbations for a frozen four-bank EMP checkpoint.

These are TRAIN-anchor-derived diagnostic interventions, NOT train examples,
not a specialist dataset, and not proof of an isolated causal carry mechanism.
Each pair changes only low-order digits with sign and high-order prefixes fixed;
analysis is paired within sign group. Opposite-sign subtraction/borrowing has its
own control and is never pooled with same-sign carrying as one treatment.
"""
from __future__ import annotations

import hashlib
import math
import random
from collections import Counter

from tools.nanojev_emp_hard_case_detector import BANK_NAMES, digit_chain, wilson

SCHEMA = 'nanojev.emp-matched-carry-intervention.v1'
TEMPLATES = ((111, 888), (222, 777))


def _shell(x):
    n = abs(int(x))
    return n.bit_length() if n else 0


def _operation_count(a, b, sign_group):
    carry_chain, borrow_chain, carries, borrows = digit_chain(a, b)
    return carries if sign_group == 'same_sign' else borrows


def make_pair(card, template=0, max_target_drift=.20):
    """Create a paired low/high chain contrast from one certified TRAIN anchor.

    High-order operand prefixes and signs stay fixed. Targets must have identical
    magnitude shells and decimal lengths, and differ by <=20% relative to their
    average magnitude. Strong chain contrast must be verified, not assumed.
    """
    a, b, _, band, anchor = map(int, card)
    if not a or not b or min(abs(a), abs(b)) < 1000:
        return None
    sign_group = 'same_sign' if (a > 0) == (b > 0) else 'opposite_sign'
    low, high = TEMPLATES[template % len(TEMPLATES)]
    am, bm = (abs(a) // 1000) * 1000, (abs(b) // 1000) * 1000
    sa, sb = (1 if a > 0 else -1), (1 if b > 0 else -1)
    if sign_group == 'same_sign':
        c_a, c_b = am + low, bm + low
        t_a, t_b = am + high, bm + high
    else:
        # For opposite signs, subtract smaller absolute operand from larger.
        # The control has no borrow in its final three digits; treatment does.
        if am == bm:  # Avoid changing the sign of the residual.
            return None
        if am > bm:
            c_a, c_b = am + high, bm + low
            t_a, t_b = am + low, bm + high
        else:
            c_a, c_b = am + low, bm + high
            t_a, t_b = am + high, bm + low
    control = (sa*c_a, sb*c_b)
    treatment = (sa*t_a, sb*t_b)
    d0, d1 = sum(control), sum(treatment)
    if not d0 or not d1 or (d0 > 0) != (d1 > 0):
        return None
    if _shell(d0) != _shell(d1) or len(str(abs(d0))) != len(str(abs(d1))):
        return None
    if abs(d0-d1) / ((abs(d0)+abs(d1))/2) > max_target_drift:
        return None
    if any(len(str(abs(c))) != len(str(abs(t))) or _shell(c) != _shell(t)
           for c,t in zip(control,treatment)):
        return None
    n0, n1 = _operation_count(*control, sign_group), _operation_count(*treatment,sign_group)
    if not (n0 <= 1 and n1 >= 3 and n1-n0 >= 2):
        return None
    return {'anchor':anchor,'band':band,'group':sign_group,'template':int(template),
            'control':(*control,d0,band,anchor),
            'treatment':(*treatment,d1,band,anchor),
            'control_chain':n0,'treatment_chain':n1,
            'relative_answer_drift':abs(d0-d1)/((abs(d0)+abs(d1))/2),
            'anchor_prefixes':[am,bm]}


def make_pairs(cards, max_per_sign, max_target_drift=.20):
    if max_per_sign < 1 or not 0 < max_target_drift <= .5:
        raise ValueError('EMP_MATCHED_BAD_PAIR_SETTINGS')
    selected={'same_sign':[], 'opposite_sign':[]}
    seen=set()
    skipped=Counter()
    for card in cards:
        anchor=int(card[4])
        if anchor in seen:
            skipped['duplicate_anchor'] += 1
            continue
        # Template selection independent of model outcomes; stable across runs.
        template=int.from_bytes(hashlib.sha256(f'emp-carry-template:v1:{anchor}'.encode()).digest()[:4],'big')%len(TEMPLATES)
        case=make_pair(card,template,max_target_drift)
        if case is None:
            skipped['unmatched'] += 1
            continue
        group=case['group']
        if len(selected[group]) >= max_per_sign:
            continue
        seen.add(anchor)
        selected[group].append(case)
        if all(len(x)>=max_per_sign for x in selected.values()):
            break
    return selected, {'attempted':len(cards),'accepted':{k:len(v) for k,v in selected.items()},
                      'skipped':dict(skipped),'target_pairs_per_group':max_per_sign}


def _correct(target, pred):
    return pred == 0 if not target else abs(pred/target - 1) <= .05+1e-12


def _percentiles(values):
    if not values:return {'p50':None,'p90':None,'p99':None}
    s=sorted(values)
    def q(x):
        p=(len(s)-1)*x; i=int(p)
        return round(s[i]+(s[min(i+1,len(s)-1)]-s[i])*(p-i),6)
    return {'p50':q(.5),'p90':q(.9),'p99':q(.99)}


def _binomial_two_sided(a,b):
    # Exact paired McNemar test, conditioned on the discordant cases.
    n=a+b
    if n==0:return 1.0
    k=min(a,b)
    terms=[math.exp(math.lgamma(n+1)-math.lgamma(j+1)-math.lgamma(n-j+1)-n*math.log(2))
           for j in range(k+1)]
    return min(1.,2*math.fsum(terms))


def paired_bank_stats(cases, control_pred, treatment_pred, bootstrap_seed=0):
    """Paired signed increase in failure risk under the chain intervention."""
    if not len(cases) or len(cases)!=len(control_pred) or len(cases)!=len(treatment_pred):
        raise ValueError('EMP_MATCHED_PREDICTION_ALIGNMENT')
    wrong_c=[];wrong_t=[]; err_c=[];err_t=[];scale_c=[];scale_t=[]
    for case,pc,pt in zip(cases,control_pred,treatment_pred):
        dc,dt=case['control'][2],case['treatment'][2]
        wrong_c.append(int(not _correct(dc,pc)))
        wrong_t.append(int(not _correct(dt,pt)))
        err_c.append(abs((pc-dc)/dc));err_t.append(abs((pt-dt)/dt))
        scale_c.append(pc/dc-1);scale_t.append(pt/dt-1)
    n=len(cases)
    worsened=sum(not c and t for c,t in zip(wrong_c,wrong_t))
    improved=sum(c and not t for c,t in zip(wrong_c,wrong_t))
    changes=[t-c for c,t in zip(wrong_c,wrong_t)]
    observed=sum(changes)/n
    rng=random.Random(f'emp-matched-bootstrap:v1:{bootstrap_seed}:{n}')
    bootstrap=sorted(sum(changes[rng.randrange(n)] for _ in range(n))/n for _ in range(1000))
    def agg(wrong,relative,scale):
        p=sum(wrong)/n
        return {'hard':sum(wrong),'hard_rate':round(p,6),'hard_rate_95pct_wilson':wilson(sum(wrong),n),
                'absolute_relative_error':_percentiles(relative),
                'scale_bias':round(sum(scale)/n,6),
                'scale_stddev':round(math.sqrt(sum((x-sum(scale)/n)**2 for x in scale)/n),6)}
    return {'n_pairs':n,'control':agg(wrong_c,err_c,scale_c),
            'treatment':agg(wrong_t,err_t,scale_t),
            'paired_failure_rate_difference':round(observed,6),
            'paired_delta_95pct_bootstrap':[round(bootstrap[24],6),round(bootstrap[975],6)],
            'worsened':worsened,'improved':improved,
            'both_wrong':sum(c and t for c,t in zip(wrong_c,wrong_t)),
            'both_correct':sum(not c and not t for c,t in zip(wrong_c,wrong_t)),
            'exact_mcnemar_p':round(_binomial_two_sided(worsened,improved),8)}


def analyze(pairs, prediction_fn, checkpoint_hash, radius, step, selected_pair):
    """prediction_fn(rows)->four-bank decoded predictions; read-only."""
    if set(selected_pair)-set(BANK_NAMES) or len(set(selected_pair))!=2:
        raise ValueError('EMP_MATCHED_BAD_SELECTED_PAIR')
    groups={}
    for group,cases in pairs.items():
        if not cases:
            groups[group]={'n_pairs':0,'status':'insufficient_matched_pairs'}
            continue
        ctrl=prediction_fn([x['control'] for x in cases]);treated=prediction_fn([x['treatment'] for x in cases])
        if set(ctrl)!=set(BANK_NAMES) or set(treated)!=set(BANK_NAMES):
            raise ValueError('EMP_MATCHED_INCOMPLETE_BANK_PREDICTIONS')
        per_bank={bank:paired_bank_stats(cases,ctrl[bank],treated[bank],bootstrap_seed=step)
                  for bank in BANK_NAMES}
        # A pair is hard only if both provisional winners miss the 5% criterion.
        def paired_hard(preds):
            return [int(all(not _correct(card[2],preds[name][i]) for name in selected_pair))
                    for i,card in enumerate(cases_cards)]
        cases_cards=[x['control'] for x in cases]; yc=paired_hard(ctrl)
        cases_cards=[x['treatment'] for x in cases]; yt=paired_hard(treated)
        # A binary paired-failure analysis can reuse paired_bank_stats with a
        # small dedicated descriptor, but do it directly to avoid fake targets.
        d=[t-c for c,t in zip(yc,yt)]
        worse=sum(v==1 for v in d);better=sum(v==-1 for v in d)
        case_examples = []
        for i, case in enumerate(cases):
            case_examples.append({
                'anchor':case['anchor'], 'template':case['template'],
                'control':{'a':case['control'][0], 'b':case['control'][1],
                           'target':case['control'][2], 'chain_positions':case['control_chain'],
                           'predictions':{name:ctrl[name][i] for name in BANK_NAMES}},
                'treatment':{'a':case['treatment'][0], 'b':case['treatment'][1],
                             'target':case['treatment'][2], 'chain_positions':case['treatment_chain'],
                             'predictions':{name:treated[name][i] for name in BANK_NAMES}},
                'pair_failure_delta':d[i],
                'relative_answer_drift':round(case['relative_answer_drift'],6)})
        # Show worsened matched pairs first, then unchanged/improved cases. These
        # are audit examples only; ranking never affects the measured cohort.
        case_examples.sort(key=lambda item:(-item['pair_failure_delta'],item['anchor']))
        rng=random.Random(f'emp-pair-group-bootstrap:v1:{checkpoint_hash}:{group}')
        boot=sorted(sum(d[rng.randrange(len(d))] for _ in d)/len(d) for _ in range(1000))
        groups[group]={'status':'measured','n_pairs':len(cases),
             'contrast':('same_sign_carry' if group=='same_sign' else 'opposite_sign_borrow'),
             'mean_chain_count':{'control':round(sum(x['control_chain'] for x in cases)/len(cases),4),
                                 'treatment':round(sum(x['treatment_chain'] for x in cases)/len(cases),4)},
             'mean_relative_answer_drift':round(sum(x['relative_answer_drift'] for x in cases)/len(cases),6),
             'provisional_pair':{'control_hard':sum(yc),'treatment_hard':sum(yt),
                   'paired_failure_rate_difference':round(sum(d)/len(d),6),
                   'worsened':worse,'improved':better,'exact_mcnemar_p':round(_binomial_two_sided(worse,better),8),
                   'paired_delta_95pct_bootstrap':[round(boot[24],6),round(boot[975],6)]},
             'audit_examples':case_examples[:8],
             'anchor_bands':dict(Counter(str(x['band']) for x in cases)),
             'answer_digit_lengths':dict(Counter(str(len(str(abs(x['control'][2])))) for x in cases)),
             'banks':per_bank,
             'templates':dict(Counter(str(x['template']) for x in cases))}
    return {'schema':SCHEMA,'checkpoint_sha256':checkpoint_hash,'radius':radius,'step':step,
            'selected_pair':list(selected_pair),'groups':groups,
            'scope':'Independent synthetic contrasts derived exclusively from permitted TRAIN anchors. '
                    'Controls and treatments are never used for optimizer updates.',
            'warning':'These are matched low-order-digit perturbations. Residual values and digit suffixes '
                      'also change; association under intervention is NOT proof that carrying alone causes '
                      'failures. Cross-sign contrasts are not matched on answer magnitude and are not '
                      'interpreted as a causal effect of sign. DEV and TEST are excluded.'}
