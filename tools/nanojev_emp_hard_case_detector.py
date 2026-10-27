#!/usr/bin/env python3
"""Interpretable, input-only hardness detector for four-bank NanoJev diagnostics.

Training labels are derived from frozen TRAIN predictions, never given as
features. Evaluation uses disjoint TRAIN anchor groups, and never touches DEV,
TEST or the specialist-reserved anchor families. Candidate explanations are
predictive associations, *not* causal claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

SCHEMA = 'nanojev.emp-hard-detector.v1'
BANK_NAMES = ('left.left', 'left.right', 'right.left', 'right.right')
LABELS = ('both_selected_wrong', 'all_four_wrong', 'two_or_more_wrong', 'left.left',
          'left.right', 'right.left', 'right.right')


def _digits(n):
    return str(abs(int(n)))


def _trailing(s, character):
    return len(s) - len(s.rstrip(character))


def _max_streak(operations):
    best = run = 0
    for hit in operations:
        run = run + 1 if hit else 0
        best = max(best, run)
    return best


def digit_chain(a, b):
    """Describe decimal carry/borrow difficulty without returning an answer."""
    aa, bb = abs(int(a)), abs(int(b))
    carry = 0
    carry_hits = []
    for i in range(max(len(str(aa)), len(str(bb)))):
        x, y = aa % 10, bb % 10
        carry = int(x + y + carry >= 10)
        carry_hits.append(bool(carry))
        aa //= 10
        bb //= 10
    aa, bb = max(abs(int(a)), abs(int(b))), min(abs(int(a)), abs(int(b)))
    borrow = 0
    borrow_hits = []
    for i in range(max(len(str(aa)), len(str(bb)))):
        x, y = aa % 10, bb % 10
        borrow = int(x - borrow < y)
        borrow_hits.append(bool(borrow))
        aa //= 10
        bb //= 10
    return _max_streak(carry_hits), _max_streak(borrow_hits), sum(carry_hits), sum(borrow_hits)


def features(a, b):
    """Only observable operand features. No target, predictions, or errors."""
    a, b = int(a), int(b)
    x, y = _digits(a), _digits(b)
    same_len = len(x) == len(y)
    opposite = (a < 0 < b) or (b < 0 < a)
    same = (a > 0 and b > 0) or (a < 0 and b < 0)
    lead = 0
    if same_len:
        for u, v in zip(x, y):
            if u != v:
                break
            lead += 1
    carry_chain, borrow_chain, carries, borrows = digit_chain(a, b)
    result = {
        'opposite_sign': opposite,
        'same_sign': same,
        'zero_operand': a == 0 or b == 0,
        'a_negative': a < 0,
        'b_negative': b < 0,
        'equal_operand_digits': same_len,
        'operand_digit_gap_ge_2': abs(len(x) - len(y)) >= 2,
        'operand_digit_gap_ge_4': abs(len(x) - len(y)) >= 4,
        'both_operands_ge_5_digits': min(len(x), len(y)) >= 5,
        'both_operands_ge_6_digits': min(len(x), len(y)) >= 6,
        'both_operands_ge_7_digits': min(len(x), len(y)) >= 7,
        'leading_digits_match_1': opposite and lead >= 1,
        'leading_digits_match_2': opposite and lead >= 2,
        'leading_digits_match_3': opposite and lead >= 3,
        'leading_digits_match_4': opposite and lead >= 4,
        'units_equal': x[-1] == y[-1],
        'tens_suffix_equal': x[-2:] == y[-2:],
        'units_sum_to_ten': (int(x[-1]) + int(y[-1])) == 10,
        'last_two_sum_to_hundred': (int(x[-2:]) + int(y[-2:])) == 100,
        'any_trailing_zero': _trailing(x, '0') >= 1 or _trailing(y, '0') >= 1,
        'trailing_zero_ge_2': _trailing(x, '0') >= 2 or _trailing(y, '0') >= 2,
        'trailing_nines_ge_2': _trailing(x, '9') >= 2 or _trailing(y, '9') >= 2,
        'repeated_digits': any(x[i] == x[i+1] for i in range(len(x)-1)) or
                           any(y[i] == y[i+1] for i in range(len(y)-1)),
        'carry_chain_ge_2': same and carry_chain >= 2,
        'carry_chain_ge_3': same and carry_chain >= 3,
        'borrow_chain_ge_2': opposite and borrow_chain >= 2,
        'borrow_chain_ge_3': opposite and borrow_chain >= 3,
        'many_carry_positions': same and carries >= 3,
        'many_borrow_positions': opposite and borrows >= 3,
    }
    return result


def detector_fold(anchor, seed):
    """Hold out complete TRAIN-mine anchors; never touch specialist reserve."""
    d = hashlib.sha256(f'emp-hard-detector-evaluation:v1:{seed}:{int(anchor)}'.encode()).digest()
    return 'evaluation' if int.from_bytes(d[:8], 'big') % 4 == 0 else 'fit'


def _gini(positives, total):
    p = positives / total
    return 2 * p * (1 - p)


def fit_tree(feature_rows, labels, depth=3, min_leaf=48):
    if not feature_rows or len(feature_rows) != len(labels):
        raise ValueError('EMP_DETECTOR_BAD_TRAIN_SET')
    names = sorted(feature_rows[0])
    if any(set(row) != set(names) for row in feature_rows):
        raise ValueError('EMP_DETECTOR_FEATURE_ALIGNMENT')
    def grow(indices, remaining):
        size = len(indices)
        hits = sum(labels[i] for i in indices)
        result = {'n': size, 'hard': hits, 'risk': hits/size}
        if not remaining or size < 2*min_leaf or hits in (0, size):
            return result
        base = _gini(hits, size)
        best = None
        for name in names:
            yes = [i for i in indices if feature_rows[i][name]]
            no_n = size - len(yes)
            if min(len(yes), no_n) < min_leaf:
                continue
            yes_hard = sum(labels[i] for i in yes)
            loss = (len(yes) * _gini(yes_hard,len(yes)) + no_n * _gini(hits-yes_hard,no_n))/size
            gain = base - loss
            if gain > 1e-7 and (best is None or gain > best[0]+1e-12):
                best = (gain,name,yes)
        if best is None:
            return result
        gain,name,yes = best
        selected = set(yes)
        no = [i for i in indices if i not in selected]
        result.update(feature=name, gain=gain,
                      yes=grow(yes, remaining-1), no=grow(no, remaining-1))
        return result
    return grow(list(range(len(labels))), depth)


def score(tree, feature):
    while 'feature' in tree:
        tree = tree['yes'] if feature[tree['feature']] else tree['no']
    return float(tree['risk'])


def score_path(tree, feature):
    path=[]
    while 'feature' in tree:
        name=tree['feature']; val=bool(feature[name]); path.append((name,val))
        tree=tree['yes'] if val else tree['no']
    return path, float(tree['risk'])


def auc(scores, labels):
    pos=sum(labels); neg=len(labels)-pos
    if not pos or not neg:
        return None
    pairs=sorted(zip(scores,labels),key=lambda x:x[0])
    rank_sum=0.0
    i=0
    while i<len(pairs):
        j=i+1
        while j<len(pairs) and pairs[j][0]==pairs[i][0]: j+=1
        avg_rank=(i+1+j)/2
        rank_sum += avg_rank * sum(l for _,l in pairs[i:j])
        i=j
    return (rank_sum-pos*(pos+1)/2)/(pos*neg)


def wilson(hits, n, z=1.96):
    if n == 0: return [None,None]
    p=hits/n
    denom=1+z*z/n
    middle=(p+z*z/(2*n))/denom
    half=z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/denom
    return [round(max(0,middle-half),4),round(min(1,middle+half),4)]


def _signature(target,prediction):
    target,prediction=int(target),int(prediction)
    if target and prediction*target<=0: return 'sign_crossing'
    error=prediction-target
    if error == 0: return 'exact'
    if abs(error) in (10,100,1000,10000,100000,1000000): return f'digit_displacement_{abs(error)}'
    return 'other_error'


def label_for(row, predicted, ix, chosen, target):
    misses = {name for name in BANK_NAMES if not (predicted[name][ix] == 0 if row[2] == 0 else
               abs(predicted[name][ix]/row[2] - 1) <= 0.05+1e-12)}
    if target == 'both_selected_wrong': return int(all(x in misses for x in chosen))
    if target == 'all_four_wrong': return int(len(misses)==4)
    if target == 'two_or_more_wrong': return int(len(misses)>=2)
    return int(target in misses)


def make_detector(rows, predicted, chosen, seed, target='both_selected_wrong',depth=3,min_leaf=48):
    """Learn features of difficulty; evaluate on unseen anchors in TRAIN-mine.

    Must receive *unfiltered* labelled scan, never the all-hard JSONL export.
    """
    if target not in LABELS or len(set(chosen))!=2 or any(x not in BANK_NAMES for x in chosen):
        raise ValueError('EMP_DETECTOR_BAD_TARGET_OR_PAIR')
    if len(rows) != len({(int(r[0]),int(r[1])) for r in rows}):
        raise ValueError('EMP_DETECTOR_DUPLICATE_OPERANDS')
    if set(predicted) != set(BANK_NAMES) or any(len(vals)!=len(rows) for vals in predicted.values()):
        raise ValueError('EMP_DETECTOR_PREDICTION_ALIGNMENT')
    fx=[features(r[0],r[1]) for r in rows]
    y=[label_for(r,predicted,i,chosen,target) for i,r in enumerate(rows)]
    partitions=[detector_fold(r[4],seed) for r in rows]
    fit=[i for i,v in enumerate(partitions) if v=='fit']
    ev=[i for i,v in enumerate(partitions) if v=='evaluation']
    if min(len(fit),len(ev))<20 or sum(y[i] for i in ev) < 2 or sum(y[i] for i in fit) < 2:
        raise ValueError('EMP_DETECTOR_INSUFFICIENT_POSITIVE_HOLDOUT')
    fit_f=[fx[i] for i in fit]; fit_y=[y[i] for i in fit]
    tree=fit_tree(fit_f,fit_y,depth,min(min_leaf,max(8,len(fit)//4)))
    scored=[score(tree,fx[i]) for i in ev]
    holdout_y=[y[i] for i in ev]
    baseline=sum(holdout_y)/len(ev)
    cutoff_count=max(1,math.ceil(len(ev)*0.1))
    top=sorted(range(len(ev)),key=lambda i:(-scored[i], i))[:cutoff_count]
    top_hits=sum(holdout_y[i] for i in top)
    precision=top_hits/len(top)
    path_groups=defaultdict(list)
    for i in ev:
        p,r=score_path(tree,fx[i])
        path_groups[tuple(p)].append(i)
    explanations=[]
    for path, ids in path_groups.items():
        n=len(ids); hits=sum(y[i] for i in ids)
        if n<10: continue
        explanations.append({'conditions':[{'feature':name,'value':v} for name,v in path],
            'evaluation_n':n,'evaluation_hard':hits,'evaluation_hard_rate':round(hits/n,4),
            'baseline_lift':round((hits/n)/baseline,3) if baseline else None,
            'rate_95pct_interval':wilson(hits,n),
            'fit_risk':round(score(tree,fx[ids[0]]),4)})
    explanations.sort(key=lambda x:(-x['evaluation_hard_rate'], -x['evaluation_n']))
    # Signatures are post-hoc descriptions, deliberately not detector inputs.
    error_signatures = Counter()
    for i in ev:
        if y[i]:
            for name in chosen:
                error_signatures[_signature(rows[i][2],predicted[name][i])] += 1
    score_auc=auc(scored,holdout_y)
    report={
        'schema':SCHEMA,'label':target,'selected_pair':list(chosen),
        'split_policy':'Only TRAIN/mine anchors; SHA-grouped heldout anchors for detector evaluation. '
                       'DEV, sealed TEST and specialist reserve never used.',
        'feature_contract':'Only a and b at scoring time. No answer, model prediction, or error is a feature.',
        'train':{'n':len(fit),'hard':sum(fit_y),'anchors':len({rows[i][4] for i in fit})},
        'evaluation':{'n':len(ev),'hard':sum(holdout_y),'anchors':len({rows[i][4] for i in ev}),
            'baseline_hard_rate':round(baseline,5),'roc_auc':round(score_auc,5) if score_auc is not None else None,
            'precision_at_top_10pct':round(precision,5),
            'recall_at_top_10pct':round(top_hits/max(1,sum(holdout_y)),5),
            'lift_at_top_10pct':round(precision/baseline,5) if baseline else None,
            'top_10pct_hard':top_hits,'top_10pct_n':len(top)},
        'predictive_signal':score_auc is not None and score_auc>=0.6 and precision>=1.3*baseline,
        'unseen_anchor_rules':explanations[:12],
        'holdout_failure_signatures':dict(error_signatures.most_common()),
        'model':{'tree':tree,'features':sorted(fx[0]),'depth':depth,'min_leaf':min_leaf},
        'warning':'Rules are heldout associations, not causal mechanisms. Confirm hypotheses with matched interventions.'
    }
    return report


def main():
    p=argparse.ArgumentParser(description='Score two operands with a pre-fitted diagnostic hard-case detector; no arithmetic execution or routing.')
    p.add_argument('--model',required=True,help='JSON from EMP diagnostic hard-detector output')
    p.add_argument('--a',type=int,required=True)
    p.add_argument('--b',type=int,required=True)
    args=p.parse_args()
    model=json.loads(Path(args.model).read_text(encoding='utf8'))
    if model.get('schema')!=SCHEMA:
        raise RuntimeError('EMP_DETECTOR_MODEL_SCHEMA_MISMATCH')
    feat=features(args.a,args.b)
    if sorted(feat)!=model['model']['features']:
        raise RuntimeError('EMP_DETECTOR_FEATURE_VERSION_DRIFT')
    path,risk=score_path(model['model']['tree'],feat)
    print('EMP_HARD_DETECTOR_PREDICTION='+json.dumps({
        'a':args.a,'b':args.b,'label':model['label'],'predicted_hard_probability':round(risk,5),
        'rules':[{'feature':n,'value':v} for n,v in path],
        'note':'Diagnostic risk estimate; not a proven cause or a router.'},sort_keys=True))


if __name__=='__main__':
    main()
