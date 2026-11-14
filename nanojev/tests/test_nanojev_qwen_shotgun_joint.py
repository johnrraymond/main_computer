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
    assert result['shotgun_eval_cases']==len(shotgun.DIAGNOSTIC_MODES)*len(joint.hdb.EVAL_VARIANTS)*6
    assert result['focus_effective_targets']['shotgun:prose']==12.


def test_matched_hdb08_diagnostic_identifies_variant_failure_without_changing_math(tmp_path):
    a=config(tmp_path)
    pol=joint._policy(a)
    _,hrows,_=joint._eval(a,pol)
    paired=shotgun.diagnostic_evaluation(hrows,per_family=6)
    assert len(paired)%len(shotgun.DIAGNOSTIC_MODES)==0
    scenarios={}
    for row in paired:
        scenarios.setdefault(row['scenario_key'],[]).append(row)
        assert row['metadata']['expected_balance']==int(row['target'].split(': ',1)[1])
        assert row['metadata']['expected_balance']==(
            row['metadata']['hawk_count']*row['metadata']['hawk_coefficient']+
            row['metadata']['dove_count']*row['metadata']['dove_coefficient'])
        assert row['matrix_key'].split(':')[0] in ('5','7','9')
    assert len(scenarios)==len(paired)//len(shotgun.DIAGNOSTIC_MODES)
    assert all(r['source']==r['metadata']['expected_hdb08_source'] for r in paired if r['mode']=='prose')
    assert all(len({r['target'] for r in rows})==1 and
               {r['mode'] for r in rows}==set(shotgun.DIAGNOSTIC_MODES) for rows in scenarios.values())
    # Synthetic inference: all direct products correct while all full payoff
    # prose wrong, on EXACTLY the same original mathematics.
    scores={}
    for row in paired:
        val=int(row['target'].split(': ',1)[1]);good=row['mode']!='prose'
        scores[frontier._key(row)]={'correct':good,
            'emitted':row['target'] if good else f'PROBLEM: {val+1}'}
    summary=shotgun.matched_comparison(paired,scores,frontier._key)
    assert summary['matched_scenarios']==len(scenarios)
    assert summary['all_modes_exact']==0
    assert summary['against_prose_payoff_matrix']['products']['mode_succeeds_hdb_style_fails']==len(scenarios)
    assert summary['against_prose_payoff_matrix']['products']['hdb_style_succeeds_mode_fails']==0
    assert summary['by_payoff_matrix']['5:2:3']['tested']>0
    assert len(summary['by_offset'])>=3
    assert shotgun.error_distribution(paired,scores,frontier._key)['by_offset']


def test_failure_profile_guides_train_only_disjoint_neighborhood(tmp_path):
    a=config(tmp_path)
    pol=joint._policy(a)
    _,hrows,_=joint._eval(a,pol)
    diagnostic=shotgun.diagnostic_evaluation(hrows,per_family=6)
    scores={joint.competency._digest(r['source']):{'correct':
        r['tier']!='HDB08' or r['offset'] != -1} for r in hrows}
    profile=shotgun.failure_profile(hrows,scores,lambda r:joint.competency._digest(r['source']))
    assert profile['offset_failures']=={'-1':3}
    forbidden={r['source'] for r in [*hrows,*diagnostic]}
    outputs=[shotgun.diagnostic_train_case(17,i,shotgun.MODES[i%8],forbidden,profile)
             for i in range(200)]
    assert len({r['source'] for r in outputs})>190
    assert all(r['source'] not in forbidden for r in outputs)
    assert all(int(r['matrix_key'].split(':')[0]) in (1,2,3,4) for r in outputs)
    assert sum(r['metadata']['offset']==-1 for r in outputs)>=120
    assert any(abs(r['metadata']['offset'])>8 for r in outputs)
    assert all(r['metadata']['expected_balance']==int(r['target'].split(': ',1)[1])
               for r in outputs)
    assert joint._compatible_policy({**pol,'shotgun_version':shotgun.VERSION},pol)
    assert not joint._compatible_policy({**pol,'shotgun_version':'unrecognized'},pol)


def test_operation_isolation_controls_exact_arithmetic_and_context(tmp_path):
    a=config(tmp_path)
    p=joint._policy(a)
    _,hrows,_=joint._eval(a,p)
    deck=shotgun.diagnostic_evaluation(hrows,per_family=6)
    assert len(deck)%len(shotgun.DIAGNOSTIC_MODES)==0
    assert set(shotgun.OPERATION_MODES).isdisjoint(shotgun.MODES)
    assert len({r['source'] for r in deck})==len(deck)
    scenarios={}
    for row in deck:
        scenarios.setdefault(row['scenario_key'],{})[row['mode']]=row
        assert row['metadata']['is_evaluation']
    for modes in scenarios.values():
        assert set(modes)==set(shotgun.DIAGNOSTIC_MODES)
        assert len({r['target'] for r in modes.values()})==1
        direct=modes['products']['source'].split('PROBLEM: ',1)[1].split('\nReturn',1)[0]
        assert direct in modes['products_with_matrix']['source']
        point=modes['point_differences']['source'].split('PROBLEM: ',1)[1].split('\nReturn',1)[0]
        assert point in modes['point_differences_with_matrix']['source']
        assert 'Payoff matrix' in modes['products_with_matrix']['source']
        assert 'Payoff matrix' in modes['point_differences_with_matrix']['source']
        assert modes['prose']['source']==modes['prose']['metadata']['expected_hdb08_source']
        assert '2*(' in modes['points_expression']['source']
        assert 'Compute exactly 2*' in modes['payoff_subtractions']['source']
    # Training remains restricted to the original 8 modes.
    assert set(shotgun.exposures())==set(shotgun.MODES)
    for row in deck:
        if row['mode'] in shotgun.OPERATION_MODES:
            with pytest.raises(ValueError,match='SHOTGUN_UNKNOWN_MODE'):
                shotgun.diagnostic_train_case(21,11,row['mode'],set())


def test_operation_boundary_statistics_and_case_trace(tmp_path):
    a=config(tmp_path)
    p=joint._policy(a)
    _,hrows,_=joint._eval(a,p)
    deck=shotgun.diagnostic_evaluation(hrows,per_family=6)
    # Synthetic outputs demonstrate a controlled, replicated pass->fail flip
    # that is attributed specifically to adding the payoff matrix.
    scores={}
    for row in deck:
        expected=int(row['metadata']['expected_balance'])
        good=(row['mode'] in ('contributions','products','point_differences'))
        scores[frontier._key(row)]={'correct':good,'emitted':
            f'PROBLEM: {expected if good else expected+1}'}
    summary=shotgun.matched_comparison(deck,scores,frontier._key)
    b=summary['operation_boundaries']['add-matrix-context-to-exact-expression']
    assert b['nonzero']['left_correct_right_wrong']==len({r['scenario_key'] for r in deck if r['metadata']['offset'] != 0})
    assert b['all']['left_correct_right_wrong']==len({r['scenario_key'] for r in deck})
    assert b['all']['left_wrong_right_correct']==0
    assert b['left']=='products'
    assert b['right']=='products_with_matrix'
    b2=summary['operation_boundaries']['add-matrix-context-to-point-differences']
    assert b2['all']['left_correct_right_wrong']==len({r['scenario_key'] for r in deck})
    trace=shotgun.operation_trace(deck,scores,frontier._key,step=360000)
    assert trace['step']==360000
    assert len(trace['scenarios'])==len({r['scenario_key'] for r in deck})
    sample=next(iter(trace['scenarios'].values()))
    assert set(sample['modes'])==set(shotgun.DIAGNOSTIC_MODES)
    assert sample['modes']['products']['signed_error']==0
    assert sample['modes']['products_with_matrix']['signed_error']==1
    assert sample['modes']['products']['source']
    assert sample['modes']['products_with_matrix']['emitted']
    assert trace['scope'].endswith('never used for supervision')


def test_v2_policy_can_migrate_but_new_unrecognized_policy_cannot(tmp_path):
    a=config(tmp_path)
    p=joint._policy(a)
    assert joint._compatible_policy({**p, 'shotgun_version':
        'nanojev.qwen.shotgun.failure-neighborhood.v2'},p)
    assert joint._compatible_policy({**p, 'shotgun_version':shotgun.VERSION},p)
    assert not joint._compatible_policy({**p, 'shotgun_version':'unknown'},p)
