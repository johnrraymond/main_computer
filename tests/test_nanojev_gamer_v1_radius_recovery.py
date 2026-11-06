"""Radius recovery tests, CPU-only; no GAMER checkpoint or Qwen download."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_radius_recovery as recovery


def synthetic(contexts=12,radius=4,width=24,kind='shared',seed=33):
    rng=np.random.default_rng(seed)
    numbers=np.arange(-radius,radius+1)
    centers=rng.normal(size=(contexts,1,width)).astype('float32')
    shared=rng.normal(size=width)
    shared/=np.linalg.norm(shared)
    if kind=='shared':change=numbers[None,:,None]*shared[None,None,:]
    elif kind=='nonlinear':change=(numbers.astype(float)**3)[None,:,None]*shared[None,None,:]
    elif kind=='different':change=numbers[None,:,None]*rng.normal(size=(contexts,1,width))
    elif kind=='constant':change=np.zeros((contexts,len(numbers),width))
    elif kind=='signblind':change=np.abs(numbers)[None,:,None]*shared[None,None,:]
    else:raise ValueError(kind)
    arr=(centers+change).astype(np.float32)
    counts=np.full((contexts,len(numbers)),40,dtype=np.int32)
    return arr,numbers,counts


def test_shared_linear_number_survives_across_unseen_games():
    a,y,c=synthetic()
    result,pred=recovery.analyze_stage(a,y,c,folds=4,rank=12,controls=1)
    assert result['status']=='COMPLETE'
    assert result['within_context_lovo']['linear']['r2_vs_zero']>0.999
    assert result['heldout_game']['linear']['r2_vs_zero']>0.99
    assert result['heldout_game']['nonlinear']['r2_vs_zero']>0.9
    assert result['heldout_game']['shuffled_training_labels_nonlinear'][0]['r2_vs_zero']<0.5
    assert result['zero_crossing']['adjacent_radius_1']['mean_zero_midpoint_error_ratio']<1e-3
    assert set(pred)=={'local_linear','local_nonlinear','cross_linear','cross_nonlinear','eligible_indices'}


def test_nonlin_coding_can_outperform_linear_heldout():
    a,y,c=synthetic(kind='nonlinear')
    r,_=recovery.analyze_stage(a,y,c,folds=4,rank=12)
    assert r['heldout_game']['nonlinear']['r2_vs_zero']>r['heldout_game']['linear']['r2_vs_zero']+0.05
    assert r['heldout_game']['nonlinear']['r2_vs_zero']>0.8


def test_within_context_coding_is_not_mistaken_for_shared_numerical_axis():
    a,y,c=synthetic(kind='different')
    r,_=recovery.analyze_stage(a,y,c)
    assert r['within_context_lovo']['linear']['r2_vs_zero']>0.99
    assert r['heldout_game']['linear']['r2_vs_zero']<0.75
    assert r['heldout_game']['nonlinear']['r2_vs_zero']<0.75


def test_constant_control_has_no_invented_recoverability():
    a,y,c=synthetic(kind='constant')
    r,_=recovery.analyze_stage(a,y,c)
    assert r['within_context_lovo']['linear']['r2_vs_zero']==0
    assert r['heldout_game']['nonlinear']['r2_vs_zero']==0
    assert r['zero_crossing']['adjacent_radius_1']['mean_zero_midpoint_error_ratio'] is None


def test_zero_line_asymmetric_signblind_encoding_is_detected():
    a,y,c=synthetic(kind='signblind')
    r,_=recovery.analyze_stage(a,y,c)
    z=r['zero_crossing']['adjacent_radius_1']
    assert z['mean_zero_midpoint_error_ratio']>0.99
    assert z['mean_signed_opposite_cosine']>0.99
    assert r['within_context_lovo']['linear']['r2_vs_zero']<0.1


def test_token_count_mismatch_is_excluded_entire_context():
    a,y,c=synthetic()
    c[0,0]=39
    r,p=recovery.analyze_stage(a,y,c)
    assert r['total_contexts']==12 and r['eligible_contexts']==11
    assert r['eligible_indices']==list(range(1,12))
    c[1:,:]=c[1:,:]+np.arange(len(y))[None,:]
    r,p=recovery.analyze_stage(a,y,c)
    assert r['status']=='INSUFFICIENT_TOKEN_MATCHED_CONTEXTS' and p is None


def test_train_basis_does_not_see_holdout_context():
    a,y,_=synthetic()
    train=np.arange(1,10)
    before=recovery._train_basis(a,y,train,rank=5)
    mutated=a.copy();mutated[0]+=1000
    after=recovery._train_basis(mutated,y,train,rank=5)
    np.testing.assert_allclose(before,after,rtol=0,atol=0)


def test_recovery_source_immutability_and_unique_report(tmp_path):
    a,y,c=synthetic(contexts=12,width=20)
    input_dir=tmp_path/'radius-activations-0001';input_dir.mkdir()
    (input_dir/'final_report.json').write_text(json.dumps({
        'schema':'nanojev.gamer.v1.radius-activations.v1',
        'status':'COMPLETE_READ_ONLY','expanded_run':'expanded-test',
        'checkpoint_step':500,'contexts':[{'game_id':f'game-{i}'} for i in range(12)]}),encoding='utf-8')
    (input_dir/'interventions.jsonl').write_text('observations immutable\n',encoding='utf-8')
    np.savez_compressed(input_dir/'activations.npz',values=y,token_counts=c,
        qwen_final_100=a,c0_input_norm=a,c0_hidden_1=np.broadcast_to(a[:,4:5],a.shape).copy())
    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in input_dir.iterdir()}
    first=recovery.run_recovery(input_dir,folds=3,rank=7,controls=1)
    second=recovery.run_recovery(input_dir,folds=3,rank=7,controls=0)
    assert first['status']=='COMPLETE_READ_ONLY' and second['status']=='COMPLETE_READ_ONLY'
    assert first['source_fingerprints_sha256']==originals
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in input_dir.iterdir()}==originals
    results=list(tmp_path.glob('radius-activations-0001-recovery-*'))
    assert len(results)==2
    assert all((x/'recovery_predictions.npz').exists() for x in results)
    assert first['per_stage']['qwen_final_100']['heldout_game']['linear']['r2_vs_zero']>0.99
    assert first['per_stage']['c0_hidden_1']['within_context_lovo']['linear']['r2_vs_zero']==0
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        recovery.run_recovery(input_dir,output=input_dir/'inside')


def test_validation_does_not_silently_ignore_nonfinite():
    a,y,c=synthetic();a[0,1,0]=np.nan
    with pytest.raises(ValueError,match='NONFINITE'):
        recovery.analyze_stage(a,y,c)


def test_cli_controls_are_explicit_and_read_only():
    from tools.nanojev_gamer_v1_staircase import parser,main
    args=parser().parse_args(['head-100-radius','--run','expanded',
        '--radius-contexts','48','--radius-width','4','--radius-recovery'])
    assert (args.radius_contexts,args.radius_width,args.radius_recovery)==(48,4,True)
    args2=parser().parse_args(['head-100-radius-recovery','--run','existing-radius-dir',
         '--radius-recovery-rank','8'])
    assert args2.radius_recovery_rank==8
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-radius-recovery','--run','existing-radius-dir','--steps','3'])
    with pytest.raises(ValueError,match='ONLY_RADIUS'):
        main(['status','--run','irrelevant','--radius-recovery'])
    with pytest.raises(ValueError,match='REQUIRE_RADIUS_RECOVERY'):
        main(['head-100-radius','--run','expanded','--radius-recovery-rank','7'])
