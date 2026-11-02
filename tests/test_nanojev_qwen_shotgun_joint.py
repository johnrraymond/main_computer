"""Regression gates for four-population Shotgun integration; no GPU required."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from tools import nanojev_qwen_shotgun as shotgun
from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_radius_ladder as ladder


def config(tmp_path, **changes):
    from test_nanojev_qwen_joint_frontier import args
    return args(tmp_path, **changes)


def test_all_shotgun_modes_share_underlying_scenarios_and_have_exact_balance():
    for index in range(40):
        rows=[shotgun.case(mode,index,seed=17) for mode in shotgun.MODES]
        assert len({r['source'] for r in rows})==len(shotgun.MODES)
        assert len({r['target'] for r in rows})==1
        assert len({r['metadata']['offset'] for r in rows})==1
        for row in rows:
            m=row['metadata']
            assert int(row['target'][9:])==m['hawk_count']*m['hawk_coefficient']+m['dove_count']*m['dove_coefficient']
            assert m['expected_balance']==-(abs(m['hawk_coefficient'])+m['dove_coefficient'])*m['offset']
            assert row['terminal_kind']=='int'
            assert row['family']=='shotgun'
        assert rows[0]['scale'] in shotgun.SCALES
    assert {shotgun.case('prose',i)['zero_side'] for i in range(11)}=={'zero','positive','negative'}
    assert {shotgun.case('prose',i)['scale'] for i in range(6)}==set(shotgun.SCALES)


def test_frozen_eval_is_disjoint_from_train_and_has_scale_mode_coverage():
    rows=shotgun.evaluation(42257,11)
    assert len(rows)==len(shotgun.MODES)*11
    assert len({r['source'] for r in rows})==len(rows)
    forbidden={r['source'] for r in rows}
    sampled=[shotgun.train_case(42257,step,shotgun.MODES[step%len(shotgun.MODES)],forbidden)
             for step in range(400)]
    assert all(r['source'] not in forbidden for r in sampled)
    assert all(not r['metadata']['is_evaluation'] for r in sampled)
    assert any(abs(r['metadata']['offset'])==1024 for r in sampled)
    assert len({r['source'] for r in sampled})>=390  # Small-scale repeats are allowed, but rare
    with pytest.raises(ValueError,match='SHOTGUN_EVAL_CASES_OUT_OF_RANGE'):
        shotgun.evaluation(10,0)


def test_shotgun_focus_and_four_population_minibatches(tmp_path):
    a=config(tmp_path)
    policy=joint._policy(a)
    t,h,_=joint._eval(a,policy)
    m=joint._math_eval(a,policy,t,h)
    sh=joint._shotgun_eval(a,policy,t,h,m)
    forbidden={r['source'] for r in [*t,*h,*m,*sh]}
    pool=joint._t_pool(a,policy,0,forbidden)
    tx,hx,mx=joint._exposures(policy)
    state={'t_exposures':tx,'h_exposures':hx,'math_exposures':mx,
           'shotgun_exposures':shotgun.exposures(),
           't_current':{'by_rung':{}},'h_current':{'by_tier':{}},
           'math_current':{'by_task_level':{}},'shotgun_current':{'by_mode':{}},
           'focus':{'shotgun:prose':20.}}
    for step in range(300):
        batch,mode,_=joint._batch(a,policy,step,state,pool,forbidden)
        assert len(batch)==4
        assert sum(r.get('family')=='shotgun' for r in batch)==1
        assert sum(r.get('family')=='hdb' for r in batch)==1
        assert sum(r.get('task') in policy['math_tasks'] for r in batch)==1
        assert sum('rung' in r and r.get('family') not in ('hdb','shotgun') and r.get('task') not in policy['math_tasks'] for r in batch)==1
        joint._add_exposures(state,batch)
    ex=state['shotgun_exposures']
    assert sum(v['examples'] for v in ex.values())==300
    assert ex['prose']['examples']>70
    assert all(v['examples']>0 for v in tx.values())
    assert all(v['examples']>0 for v in hx.values())
    assert all(v['examples']>0 for v in mx.values())
    assert joint._parse_focus('[SHOTGUN=3,shotgun:prose=20]',policy)=={'shotgun':3.,'shotgun:prose':20.}
    with pytest.raises(ValueError,match='JOINT_FOCUS_UNKNOWN_TARGET'):
        joint._parse_focus('shotgun:invalid=3',policy)


def test_shotgun_metrics_show_mode_scale_and_normalized_error():
    rows=shotgun.evaluation(99,11)
    correct={frontier._key(r):{'correct':True,'emitted':r['target']} for r in rows}
    perturbed={}
    for r in rows:
        expected=r['metadata']['expected_balance']
        unit=abs(r['metadata']['hawk_coefficient'])+r['metadata']['dove_coefficient']
        perturbed[frontier._key(r)]={'correct':False,'emitted':f'PROBLEM: {expected+unit}'}
    summary=shotgun.metrics(rows,correct,perturbed,frontier._key)
    assert summary['total']['tested']==len(rows)
    assert summary['total']['lost']==len(rows)
    assert set(summary['by_mode'])==set(shotgun.MODES)
    assert all(mode in summary['by_mode'] for mode in shotgun.MODES)
    dist=shotgun.error_distribution(rows,perturbed,frontier._key)
    assert dist['total']['numeric_outputs']==len(rows)
    assert dist['total']['median_absolute_error_offset_units']==1
    assert all(x['median_absolute_error_offset_units']==1 for x in dist['by_mode'].values())
    assert all(x['robust_variance_signed_log_offset_error']>=0 for x in dist['by_scale'].values())
    assert len(dist['by_scale'])==len(shotgun.SCALES)


def test_pre_shotgun_policy_migrates_without_policy_drift(tmp_path):
    a=config(tmp_path)
    policy=joint._policy(a)
    prior=dict(policy)
    prior.pop('shotgun_version');prior.pop('shotgun_eval_per_mode')
    assert joint._compatible_policy(prior,policy)
    assert not joint._compatible_policy(prior,{**policy,'math_levels':3})
    # Once trained under Shotgun, evaluation policy is pinned.
    assert not joint._compatible_policy(policy,{**policy,'shotgun_eval_per_mode':7})
    a.joint_shotgun_eval_per_mode=0
    with pytest.raises(ValueError,match='JOINT_FRONTIER_INVALID_POLICY'):
        joint._policy(a)


def test_shotgun_preview_is_four_populations():
    result=ladder.main(['--stage','preview','--frontier','--frontier-hdb',
                        '--radii','0,1,2,3,4','--frontier-max-radius','64',
                        '--focus','shotgun:prose=12'])
    assert result['optimizer_populations']==('T','HDB','mathematics','shotgun')
    assert result['shotgun_eval_cases']==len(shotgun.MODES)*6
    assert result['focus_effective_targets']['shotgun:prose']==12.
