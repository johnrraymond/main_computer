"""Synthetic behavior, strict holdouts, nulls, CLI and read-only report tests."""
import json
from pathlib import Path
import numpy as np
import pytest
from tools import nanojev_gamer_v1_shared_information as shared
from tools import nanojev_gamer_v1_number_distance as distance


def synthetic(kind='shared',games=8,dim=32,noise=0.01):
    rng=np.random.default_rng(42)
    values=np.arange(-8,9,dtype=np.int32)
    a=np.zeros((games,3,len(values),dim),dtype=np.float32)
    offsets=rng.normal(size=(games,3,dim)).astype(np.float32)*0.4
    shared_direction=rng.normal(size=dim).astype(np.float32)
    shared_direction/=np.linalg.norm(shared_direction)
    by_game=rng.normal(size=(games,dim)).astype(np.float32)
    for g in range(games):
        for f in range(3):
            for i,n in enumerate(values):
                if kind=='shared':
                    code=n*shared_direction*3
                elif kind=='game_specific':
                    code=n*by_game[g]*0.7
                elif kind=='form_specific':
                    code=n*np.roll(shared_direction,f+1)*3
                elif kind=='lexical':
                    # Different number and written form yields unrelated codes.
                    code=rng.normal(size=dim).astype(np.float32)*3
                elif kind=='curve':
                    phase=0.1*g+0.2*f
                    code=np.zeros(dim,dtype=np.float32)
                    code[0]=6*np.cos(0.55*n+phase)
                    code[1]=6*np.sin(0.55*n+phase)
                elif kind=='constant':code=np.zeros(dim)
                else:raise ValueError(kind)
                a[g,f,i]=offsets[g,f]+code+noise*rng.normal(size=dim)
    counts=np.full(a.shape[:3],45,dtype=np.int32)
    return a,counts,values,[f'game_{i}' for i in range(games)]


def test_outer_inner_numerals_disjoint_and_matched_relation_provenance():
    a,n,v,g=synthetic()
    x,y,m=shared._rows(a,n,v,range(4),('digits','signed'),('positive','negative'),'outer')
    xx,yy,mm=shared._rows(a,n,v,range(4,8),('words',),('positive','negative'),'inner')
    assert set(y)=={1,2,3} and set(yy)=={1,2,3}
    outer={p[k] for p in m for k in ('start','end')}
    inner={p[k] for p in mm for k in ('start','end')}
    assert not outer.intersection(inner)
    assert {p['game'] for p in m}.isdisjoint({p['game'] for p in mm})
    assert not any(p['token_count_changed'] for p in mm)


def test_positives_are_cross_game_negatives_same_game_and_form():
    a,n,v,g=synthetic()
    x,y,m=shared._rows(a,n,v,range(4),('digits','signed'),('positive','negative'),'outer')
    aa,pp,nn=shared._triplets(y,m,seed=23,samples=100)
    assert len(aa)==100
    for i,p,n in zip(aa,pp,nn):
        assert y[i]==y[p] and m[i]['game']!=m[p]['game']
        assert y[i]!=y[n]
        assert all(m[i][k]==m[n][k] for k in ('game','form','sign'))


def test_shared_info_transfers_when_underlying_code_is_shared():
    a,n,v,g=synthetic()
    result=shared.analyze_shared(a,n,v,g,folds=2,steps=70,controls=0,
                                 sketch_dim=32,arms=('new_games_values_words',))
    out=result['arms']['new_games_values_words']
    assert out['match_accuracy']>0.80,out
    assert out['heldout_predictive_information_bits']>0.1,out


def test_lexical_and_context_only_do_not_earn_strict_credit():
    for kind in ('constant','game_specific','lexical'):
        a,n,v,g=synthetic(kind,noise=0.0)
        out=shared.analyze_shared(a,n,v,g,folds=2,steps=50,controls=0,
               sketch_dim=32,arms=('new_games_values_words',))['arms']['new_games_values_words']
        assert out['match_accuracy']<0.65,(kind,out)


def test_spelling_dependence_fails_cross_form_even_when_same_form_works():
    a,n,v,g=synthetic('form_specific',noise=0.0)
    out=shared.analyze_shared(a,n,v,g,folds=2,steps=60,controls=0,
             sketch_dim=32,arms=('new_games_values_words',))['arms']['new_games_values_words']
    assert out['match_accuracy']<0.75,out



def test_nonlinear_curved_number_manifold_can_share_distance_without_common_direction():
    a,n,v,g=synthetic('curve',games=12,noise=0.0)
    result=shared.analyze_shared(a,n,v,g,folds=3,steps=90,controls=0,
            sketch_dim=32,arms=('new_games_values_words',))
    out=result['arms']['new_games_values_words']
    assert out['match_accuracy']>0.70,out

def test_sketch_uses_all_input_features_and_is_deterministic():
    a=np.zeros((2,3,17,102400),dtype=np.float32)
    a[...,102399]=1.0
    one,m1=shared._sample_sketch(a,dim=128,seed=74)
    two,m2=shared._sample_sketch(a,dim=128,seed=74)
    assert m1==m2 and np.array_equal(one,two)
    assert np.count_nonzero(one)==2*3*17
    assert m1['source_dims']==102400


def test_shuffled_matching_refit_can_be_computed():
    a,n,v,g=synthetic(games=8)
    out=shared.analyze_shared(a,n,v,g,folds=2,steps=20,controls=1,
           sketch_dim=32,arms=('new_games_values_words',))
    r=out['arms']['new_games_values_words']
    assert len(r['random_match_controls_bits'])==1
    assert r['empirical_p_upper_bound'] in (0.5,1.0)


def test_zero_spelling_token_mismatch_counted_not_dropped():
    a,n,v,g=synthetic()
    n[:,2,np.where(v==0)[0][0]]=55
    xa,ya,m=shared._rows(a,n,v,range(4),('words',),('positive',),'inner',zero_cross=True)
    assert any(p['token_count_changed'] for p in m)
    assert any(p['start']<=0<=p['end'] for p in m)



def test_shared_zero_crossing_is_evaluated_without_equal_token_count_requirement():
    a,n,v,g=synthetic('shared',games=8,noise=0.0)
    # Word "zero" changes token count compared with surrounding word numerals.
    n[:,2,np.where(v==0)[0][0]]=60
    output=shared.analyze_shared(a,n,v,g,folds=2,steps=60,controls=0,
        sketch_dim=32,arms=('unseen_zero_crossing_words',))
    r=output['arms']['unseen_zero_crossing_words']
    assert r['status']=='COMPLETE'
    assert r['token_count_changed_cases']>0
    assert r['match_accuracy']>.9
    assert 0<r['heldout_within_game_permutation_p']<=0.05

def test_run_read_only_report_and_overlapping_path_rejected(tmp_path):
    a,n,v,g=synthetic()
    src=tmp_path/'capture';src.mkdir()
    original={'schema':distance.SCHEMA,'status':'COMPLETE_READ_ONLY',
              'source_run':str(tmp_path/'source'),'expanded_run':str(tmp_path/'expanded'),
              'contexts':[{'game_id':x} for x in g],'checkpoint_step':500}
    (src/'final_report.json').write_text(json.dumps(original))
    (src/'interventions.jsonl').write_text('synthetic\n')
    np.savez_compressed(src/'activations.npz',values=v,token_counts=n,
                        embedding_mean100=a,qwen_final_mean100=a)
    saved={p.name:p.read_bytes() for p in src.iterdir()}
    result=shared.run_shared(src,stages=('embedding_mean100',),folds=2,
                             steps=18,controls=0,sketch_dim=32)
    assert result['status']=='COMPLETE_READ_ONLY'
    assert result['by_stage']['embedding_mean100']['arms']['new_games_values_words']['cases']>0
    assert all(p.read_bytes()==saved[p.name] for p in src.iterdir())
    with pytest.raises(ValueError,match='OVERLAPS'):
        shared.run_shared(src,output=src/'illegal',stages=('embedding_mean100',),folds=2,
                          steps=1,controls=0)


def test_cli_has_read_only_mode():
    from tools.nanojev_gamer_v1_staircase import parser,main
    p=parser().parse_args(['head-100-shared-information','--run','X','--shared-steps','5'])
    assert p.shared_steps==5
    with pytest.raises(ValueError,match='READ_ONLY'):
        main(['head-100-shared-information','--run','X','--steps','1'])
    with pytest.raises(ValueError,match='ONLY_SHARED_INFORMATION'):
        main(['head-100-number-distance','--run','X','--shared-steps','5'])
