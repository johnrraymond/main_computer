"""Shotgun expression×matrix read-only probe. No live Qwen weights required."""
import json
import re
from pathlib import Path
from collections import defaultdict
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_shotgun as sg
from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_radius_ladder as ladder
from test_nanojev_qwen_shotgun_joint import config


def deck(tmp_path):
    a=config(tmp_path)
    p=joint._policy(a)
    _,hr,_=joint._eval(a,p)
    return a,hr,sg.coefficient_probe_evaluation(hr,per_family=6)


def test_factored_cases_preserve_original_v3_controls_and_targets(tmp_path):
    _,h,rows=deck(tmp_path)
    canonical=sg.diagnostic_evaluation(h,per_family=6)
    expected={(r['scenario_key'],r['mode']):r for r in canonical if r['mode'] in ('products','products_with_matrix')}
    by=defaultdict(dict)
    for row in rows:
        by[row['scenario_key']][row['mode']]=row
        assert row['metadata']['factorial_only']
        assert row['metadata']['is_evaluation']
        assert row['target']==f"PROBLEM: {row['metadata']['expected_balance']}"
        assert row['source']
    assert len({r['source'] for r in rows})==len(rows)
    assert len(rows)==len(by)*len(sg.COEFFICIENT_PROBE_MODES)
    assert all(set(modes)==set(sg.COEFFICIENT_PROBE_MODES) for modes in by.values())
    assert set(sg.COEFFICIENT_PROBE_MODES)-set(sg.MODES)
    for scene,modes in by.items():
        for name in ('products','products_with_matrix'):
            assert modes[name]['source']==expected[scene,name]['source']
        orig=modes['products']
        v=int(orig['metadata']['expected_balance'])
        for name,row in modes.items():
            assert row['metadata']['expected_balance']==v*row['factor_expression']
            assert row['metadata']['expected_balance']==(
                row['metadata']['hawk_count']*row['metadata']['hawk_coefficient']+
                row['metadata']['dove_count']*row['metadata']['dove_coefficient'])
        same=modes['products_with_matrix']['source']
        shifted=modes['products_with_matrix_hh_shift']['source']
        # Exactly one payoff number changes, not the arithmetic expression.
        assert same.replace('HH=', 'HH=',1).split('\n',1)[1]==shifted.split('\n',1)[1]
        assert same.split('\n')[0]!=shifted.split('\n')[0]
        assert modes['products_doubled_with_matrix']['source'].split('\n',1)[0]==same.split('\n',1)[0]
        assert 'Irrelevant reference numbers' in modes['products_with_numeric_context']['source']
        assert 'do not recompute coefficients' in modes['products_with_matrix_explicitly_ignored']['source']
    # None of the new modes may be selected as an optimizer training case.
    for mode in set(sg.COEFFICIENT_PROBE_MODES)-set(sg.MODES):
        with pytest.raises(ValueError,match='SHOTGUN_UNKNOWN_MODE'):
            sg.diagnostic_train_case(42,1,mode,set())


def test_factorial_report_quantifies_independent_factor_effects(tmp_path):
    _,_,rows=deck(tmp_path)
    scores={}
    for r in rows:
        baseline=int(r['metadata']['expected_balance'])//r['factor_expression']
        mode=r['mode']
        # Synthetic persistent 80% slope with original matrix, 90% with
        # altered HH. Correct outside payoff context.
        if mode in ('products','products_doubled','products_with_numeric_context',
                    'products_with_matrix_explicitly_ignored'):
            predicted=int(r['metadata']['expected_balance'])
        elif r['factor_matrix_hh_shift_points']:
            predicted=baseline*r['factor_expression']*9//10
        else:
            predicted=baseline*r['factor_expression']*8//10
        scores[frontier._key(r)]={'emitted':f'PROBLEM: {predicted}',
                                  'correct':predicted==r['metadata']['expected_balance']}
    report=sg.coefficient_probe_report(rows,scores,frontier._key,step=361500,checkpoint_sha256='sha')
    assert report['matched_scenarios']==len(rows)//len(sg.COEFFICIENT_PROBE_MODES)
    assert report['evaluation_only'] and report['optimizer_updates']==0
    assert report['contrasts']['add_original_matrix']['nonzero']['left_correct_right_wrong']>0
    assert report['contrasts']['change_matrix_same_expression']['nonzero']['numeric_pairs']>0
    assert report['contrasts']['add_unrelated_numbers']['all']['left_correct_right_wrong']==0
    assert report['contrasts']['tell_model_matrix_is_irrelevant']['nonzero']['left_wrong_right_correct']>0
    assert any(v['matrix_changed_expression_fixed']!=0 for v in report['factorial_differences']
               if v['original_expected']!=0)
    sample=report['scenarios']['5:2:3:1']
    assert sample['modes']['products']['parsed_integer']==-25
    assert sample['modes']['products_with_matrix']['parsed_integer']==-20
    assert sample['modes']['products_doubled_with_matrix']['parsed_integer']==-40
    assert sample['modes']['products_with_matrix_hh_shift']['parsed_integer']==-23
    assert sample['modes']['products_with_matrix']['signed_error']==5
    assert sample['modes']['products_doubled']['expected']==-50


def test_zero_packet_is_not_mistaken_for_arithmetic_failure(tmp_path):
    _,_,rows=deck(tmp_path)
    scores={}
    for r in rows:
        val=int(r['metadata']['expected_balance'])
        emitted='PROBLEM: -0' if val==0 else f'PROBLEM: {val}'
        scores[frontier._key(r)]={'emitted':emitted,'correct':val!=0}
    result=sg.coefficient_probe_report(rows,scores,frontier._key,step=1,checkpoint_sha256='x')
    entry=next(s for s in result['scenarios'].values() if s['original_expected']==0)
    assert entry['modes']['products_with_matrix']['negative_zero_packet']
    assert entry['modes']['products_with_matrix']['parsed_integer'] is None


def test_readonly_snapshot_invocation_does_not_update_checkpoint(tmp_path,monkeypatch):
    import torch
    a,hr,rows=deck(tmp_path)
    out=joint._out(a)
    out.mkdir(parents=True,exist_ok=True)
    cp=out/'working.pt'
    # Lightweight synthetic checkpoint with true serialization and hardlinking.
    original_v3=sg.diagnostic_evaluation(hr,per_family=6)
    saved={'schema':joint.SCHEMA,'state':{'step':361500,'parent_sha256':'parenthash',
        'policy':{'hdb':{},'shotgun_eval_per_mode':6},
        'shotgun_diagnostic_version':sg.DIAGNOSTIC_VERSION,
        'shotgun_eval_sha256':ladder.deck_fingerprint(original_v3)},'lora':{'anything':torch.ones(1)},
        'optimizer':{'unchanged':torch.ones(1)}}
    torch.save(saved,cp)
    before=cp.read_bytes()
    ladder._atomic_json(out/'shotgun_eval_deck.json',original_v3)
    ladder._atomic_json(out/'hdb_eval_deck.json',hr)
    class FakeBB:
        def eval(self):return self
    class FakeZero:
        def restore_lora(self,bb,weights):assert torch.equal(weights['anything'],torch.ones(1))
    monkeypatch.setattr(joint.hdb,'_checkpoint',lambda args,policy:({'x':1},'parenthash'))
    monkeypatch.setattr(joint.hdb,'_model',lambda args,parent:(FakeBB(),None,FakeZero(),None))
    monkeypatch.setattr(joint,'_score_shotgun',lambda bb,tok,deck,args:{
        frontier._key(r):{'correct':True,'emitted':r['target']} for r in deck})
    result=joint.rescore_shotgun_factorial(a)
    assert cp.read_bytes()==before
    assert result['checkpoint_step']==361500
    assert result['matched_scenarios']>=1
    assert result['optimizer_updates']==0
    report_path=Path(result['report'])
    assert report_path.is_file()
    assert json.loads(report_path.read_text())['checkpoint_sha256']==result['checkpoint_sha256']
    assert not list(report_path.parent.glob('checkpoint_snapshot.pt'))
    assert not (out/'progress.json').exists()
    assert not (out/'joint_frontier_report.json').exists()


def test_factorial_stage_routes_without_starting_optimizer(monkeypatch,tmp_path):
    a=config(tmp_path)
    sentinel={'evaluation_only':True,'stage':'shotgun-factorial'}
    monkeypatch.setattr(joint,'rescore_shotgun_factorial',lambda args:sentinel)
    response=ladder.main(['--stage','shotgun-factorial','--frontier','--frontier-hdb',
        '--radii','0,1,2,3,4','--frontier-max-radius','64',
        '--stage1-checkpoint','stage1.pt','--text-checkpoint','text.pt'])
    assert response==sentinel
    with pytest.raises(ValueError,match='JOINT_FOCUS_REQUIRES_JOINT_FRONTIER_RUN_OR_PREVIEW'):
        ladder.main(['--stage','shotgun-factorial','--frontier','--frontier-hdb',
                     '--focus','[HDB08=12]'])
