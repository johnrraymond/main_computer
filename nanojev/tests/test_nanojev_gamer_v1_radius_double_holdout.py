"""CPU-only leakage, geometry, zero, provenance, and CLI tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_radius_double_holdout as probe


def sample(kind='shared',contexts=12,radius=4,width=24,seed=5):
    rng=np.random.default_rng(seed)
    values=np.arange(-radius,radius+1,dtype=np.int32)
    names=[f'game_{i}' for i in range(contexts)]
    context_offset=rng.normal(0,0.02,size=(contexts,1,width))
    direction=rng.normal(size=(width,));direction/=np.linalg.norm(direction)
    if kind=='shared':
        signal=values[None,:,None]*direction[None,None,:]
    elif kind=='nonlinear':
        signal=(values[None,:,None]**3/16)*direction[None,None,:]
    elif kind=='distinct_numbers':
        table=rng.normal(0,1,size=(len(values),width))
        signal=table[None,:,:]
    elif kind=='context_only':
        signal=values[None,:,None]*rng.normal(size=(contexts,1,width))
    elif kind=='constant':
        signal=np.zeros((1,len(values),width))
    else:raise ValueError(kind)
    activations=(context_offset+signal).astype(np.float32)
    lengths=np.full(activations.shape[:2],40,dtype=np.int32)
    return activations,values,lengths,names


def test_shared_axis_predicts_unseen_numerals_in_unseen_games_and_unseen_zero():
    a,y,counts,games=sample()
    report,pred=probe.analyze_stage_double_holdout(a,y,counts,games,folds=4,controls=1)
    assert report['status']=='COMPLETE'
    assert report['experiments']['even_to_odd']['train_values']==[-4,-2,0,2,4]
    assert report['experiments']['even_to_odd']['heldout_values']==[-3,-1,1,3]
    assert report['experiments']['odd_to_even']['zero_is_unseen']
    for experiment in report['experiments'].values():
        assert experiment['modes']['inductive']['linear']['r2_vs_zero']>0.95
        for fold in experiment['folds']:
            assert not set(fold['training_game_names']).intersection(fold['heldout_game_names'])
            assert not set(fold['train_values']).intersection(fold['heldout_values'])
    unseen=report['experiments']['odd_to_even']['modes']['inductive']['linear']['per_value']['0']
    assert unseen['cases']==12
    assert unseen['mae']<0.5
    # Unlabeled centering may make zero appear exact by symmetry; the
    # *inductive* zero result is the independent measurement.
    assert report['experiments']['even_to_odd']['modes']['inductive']['retrained_shuffled_label_controls_nonlinear'][0]['r2_vs_zero']<0.6
    assert all(x.shape==(12,9) for x in pred.values())


def test_numeric_token_identity_is_not_shared_magnitude():
    a,y,counts,games=sample(kind='distinct_numbers',seed=61)
    report,_=probe.analyze_stage_double_holdout(a,y,counts,games)
    # Each literal has a perfectly reproducible code across games. Because
    # test literal identities were NEVER available at fit time, that alone
    # does not generalize as a numerical magnitude coordinate.
    r=report['experiments']['even_to_odd']['modes']['inductive']['linear']['r2_vs_zero']
    assert r<0.5


def test_context_specific_axis_does_not_transfer():
    a,y,counts,games=sample(kind='context_only')
    report,_=probe.analyze_stage_double_holdout(a,y,counts,games)
    assert report['experiments']['even_to_odd']['modes']['inductive']['linear']['r2_vs_zero']<0.6


def test_constant_layer_does_not_fabricate_numerical_signal():
    a,y,counts,games=sample(kind='constant')
    report,_=probe.analyze_stage_double_holdout(a,y,counts,games)
    assert report['experiments']['even_to_odd']['modes']['inductive']['linear']['r2_vs_zero']==0.0


def test_mismatched_token_count_exclusion_and_nonfinite_failure():
    a,y,counts,games=sample()
    counts[2,0]+=1
    report,_=probe.analyze_stage_double_holdout(a,y,counts,games)
    assert report['eligible_contexts']==11
    a[3,0,0]=np.nan
    with pytest.raises(ValueError,match='NONFINITE'):
        probe.analyze_stage_double_holdout(a,y,counts,games)


def test_duplicate_game_ids_are_grouped_together():
    a,y,c,g=sample(contexts=16)
    g[1]=g[0]
    report,_=probe.analyze_stage_double_holdout(a,y,c,g)
    for experiment in report['experiments'].values():
        for f in experiment['folds']:
            assert not set(f['training_game_names']).intersection(f['heldout_game_names'])


def test_end_to_end_outputs_are_sibling_and_sources_immutable(tmp_path):
    a,y,c,g=sample()
    source=tmp_path/'radius-activations-0002'
    source.mkdir()
    report={'schema':'nanojev.gamer.v1.radius-activations.v1','status':'COMPLETE_READ_ONLY',
            'expanded_run':str(tmp_path/'expanded'), 'checkpoint_step':500,
            'contexts':[{'game_id':game} for game in g]}
    (source/'final_report.json').write_text(json.dumps(report))
    (source/'interventions.jsonl').write_text('synthetic source\n')
    np.savez_compressed(source/'activations.npz',values=y,token_counts=c,
                        embedding_mean100=a,c0_hidden_1=a*0.5)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    one=probe.run_double_holdout(source,stages=('embedding_mean100','c0_hidden_1'))
    two=probe.run_double_holdout(source,stages=('embedding_mean100',),controls=0)
    assert one['status']=='COMPLETE_READ_ONLY'
    assert one['summary']['layer_comparison']['embedding_mean100']['odd_to_even']['inductive']['linear']>0.98
    assert len(list(tmp_path.glob('radius-activations-0002-double-holdout-*')))==2
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        probe.run_double_holdout(source,output=source/'subfolder',stages=('embedding_mean100',))
    with pytest.raises(ValueError,match='OVERLAPS_SOURCE'):
        probe.run_double_holdout(source,output=tmp_path/'expanded'/'reports',stages=('embedding_mean100',))
    assert two['retrained_null_controls']==0


def test_cli_read_only_and_flags():
    from tools.nanojev_gamer_v1_staircase import parser,main
    args=parser().parse_args(['head-100-radius-double-holdout','--run','x',
          '--radius-double-folds','3','--radius-double-stages','embedding_mean100,c0_hidden_1'])
    assert args.radius_double_folds==3
    assert args.radius_double_stages=='embedding_mean100,c0_hidden_1'
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-radius-double-holdout','--run','x','--steps','1'])
    with pytest.raises(ValueError,match='FLAGS_ONLY'):
        main(['status','--run','x','--radius-double-folds','3'])
