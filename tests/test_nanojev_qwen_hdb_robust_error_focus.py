"""Scale-safe HDB07/08 quality observations with unchanged exact grading."""
import json
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_hdb_balance as hdb
from tools import nanojev_qwen_competency_diagnostic as competency
from tools import nanojev_qwen_hdb_difficulty as difficulty
from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_lm_radius_ladder as ladder


def scored(rows, shifts):
    """Displace a known expected balance in one-encounter units."""
    scores={}
    for row,offset in zip(rows,shifts):
        scale=abs(row['hawk_coefficient'])+abs(row['dove_coefficient'])
        observed=row['delta']+offset*scale
        text=f'PROBLEM: {observed}'
        scores[competency._digest(row['source'])]={
            'correct':text==row['target'],'emitted':text,
            'numerical_terminal':observed}
    return scores


def examples():
    variants=((1,1,3),(5,2,3),(9,1,4))
    return [hdb._row(tier,k,variants[idx%3],label='HELDOUT')
            for tier in (7,8) for idx,k in enumerate(range(-13,14))]


def test_normalized_error_and_approximate_sign_not_exact_grading():
    rows=[hdb._row(7,1,(1,1,3),label='x'),
          hdb._row(8,1,(9,1,3),label='x')]
    grades=scored(rows,[1,1])
    report=difficulty.balance_error_distribution(rows,grades)
    assert report['schema']==difficulty.BALANCE_ERROR_SCHEMA
    for tier in ('HDB07','HDB08'):
        metric=report['by_tier'][tier]
        assert metric['exact_correct']==0
        assert metric['median_absolute_error_offset_units']==1
        assert metric['within_one_offset']==1
        assert metric['within_one_offset_rate_of_all']==1
        assert metric['robust_variance_signed_log_offset_error']==0
    # Strict output scoring remains unchanged, regardless of proximity.
    assert all(not grade['correct'] for grade in grades.values())


def test_robust_median_mad_ignore_large_scale_jump_and_tail_stays_visible():
    rows=examples()
    # Most estimates within 1 offset, a minority within 2, plus one
    # catastrophic magnitude jump to prove it cannot dominate the median/MAD.
    offsets=[0]*8+[1]*13+[-2]*5+[1_000_000_000]
    assert len(offsets)==27
    scores=scored(rows[:27],offsets)
    metrics=difficulty.balance_error_distribution(rows[:27],scores)['by_tier']['HDB07']
    assert metrics['tested']==27 and metrics['numeric_outputs']==27
    assert metrics['median_absolute_error_offset_units']==1
    assert metrics['p90_absolute_error_offset_units']==2
    assert metrics['max_absolute_error_offset_units']==1_000_000_000
    assert metrics['outputs_over_ten_offsets']==1
    assert set(metrics['by_side'])=={'negative','zero','positive'}
    assert metrics['robust_variance_signed_log_offset_error']>0
    assert metrics['robust_variance_signed_log_offset_error']<10


def test_shrinking_error_distribution_independent_of_exact_score():
    rows=examples()
    old=scored(rows,[10 if i%2 else -10 for i in range(len(rows))])
    new=scored(rows,[2 if i%2 else -2 for i in range(len(rows))])
    old_metrics=difficulty.balance_error_distribution(rows,old)
    new_metrics=difficulty.balance_error_distribution(rows,new)
    change=difficulty.compare_balance_distributions(old_metrics,new_metrics)
    for tier in ('HDB07','HDB08'):
        assert old_metrics['by_tier'][tier]['exact_accuracy']==0
        assert new_metrics['by_tier'][tier]['exact_accuracy']==0
        assert change[tier]['deltas']['median_absolute_error_offset_units']==-8
        assert change[tier]['robust_spread_shrinking'] is True
        assert change[tier]['deltas']['robust_variance_signed_log_offset_error']<0


def test_malformed_terminal_excluded_from_medians_but_fails_accuracy():
    rows=[hdb._row(8,k,(1,1,3),label='x') for k in (-1,0,1)]
    grades=scored(rows,[0,0,0])
    grades[competency._digest(rows[-1]['source'])]={
        'correct':False,'emitted':'PROBLEM: (1+2)'}
    stats=difficulty.balance_error_distribution(rows,grades)['by_tier']['HDB08']
    assert stats['numeric_outputs']==2 and stats['non_numeric_outputs']==1
    assert stats['exact_correct']==2
    assert stats['within_one_offset_rate_of_all']==2/3
    old=difficulty.balance_error_distribution(rows,scored(rows,[1,1,1]))
    new=difficulty.balance_error_distribution(rows,grades)
    assert difficulty.compare_balance_distributions(old,new)['HDB08']['robust_spread_shrinking'] is None


def test_error_metrics_attached_to_heldout_failure_records_and_focus_feedback():
    rows=examples()
    grades=scored(rows,[1]*len(rows))
    diagnostics,failures=difficulty.analyze_hdb(rows,grades,grades)
    assert len(failures)==len(rows)
    assert all(f['balance_error']['signed_error_in_offset_units']==1 for f in failures)
    assert diagnostics['balance_error_distribution']['by_tier']['HDB08']['tested']==27
    policy={'math_tasks':('algebra',),'math_levels':1}
    state={'focus':{'HDB07':5,'HDB08':8},'step':100,'last_checked_step':100,
           't_exposures':{},'h_exposures':{},'math_exposures':{},
           'hdb_balance_error_current':diagnostics['balance_error_distribution']}
    joint._activate_focus(state,policy,{'HDB07':6,'HDB08':10})
    nextgrades=scored(rows,[0]*len(rows))
    state['hdb_balance_error_current']=difficulty.balance_error_distribution(rows,nextgrades)
    state['step']=200
    summary=joint._focus_feedback(state,policy)
    assert summary['targets']['HDB08']['weight']==10
    assert summary['hdb_approximate_balance']['change_since_focus']['HDB08']['deltas']['median_absolute_error_offset_units']==-1
    assert state['focus_baseline_balance_error']['by_tier']['HDB08']['exact_correct']==0


def test_rescore_lookback_uses_only_prior_matching_deck_and_never_writes_training(tmp_path,monkeypatch):
    """Exercise checkpoint comparison through the existing pure helper inputs."""
    rows=examples()
    old=difficulty.balance_error_distribution(rows,scored(rows,[2]*54))
    new=difficulty.balance_error_distribution(rows,scored(rows,[1]*54))
    assert difficulty.compare_balance_distributions(old,new)['HDB07']['deltas']['p90_absolute_error_offset_units']==-1
    corrupted={**old,'schema':'old-grading-schema'}
    assert difficulty.compare_balance_distributions(corrupted,new) is None
