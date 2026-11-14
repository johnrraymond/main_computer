"""Offline deterministic tests for the 5%-of-actual hierarchical experiment."""
from pathlib import Path
import json
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_hierarchical_activation_decoder as exp


def test_5pct_is_per_actual_not_global_range():
    true=np.array([-2_000_000.,-10.,-1.,0.,1.,10.,2_000_000.])
    predictions=np.array([-1_900_000.,-9.5,-.95,0.,1.05,10.51,1_900_000.])
    report,passed,rel=exp.metrics(true,predictions,(200,))
    assert report['within_5pct_count']==5 # 10.51 fails; boundary of 5% floats handled
    assert passed[0] and passed[1] and passed[2] and passed[3] and passed[4] and passed[6]
    assert not passed[5]
    assert report['zero_exact']
    assert np.isclose(rel[5],.051)


def test_zero_and_opposite_sign_must_fail():
    true=np.array([-1.,0.,1.])
    wrong=np.array([1.,.01,1.])
    report,passed,_=exp.metrics(true,wrong,(200,))
    assert list(passed)==[False,False,True]
    assert not report['zero_exact'] and report['sign_accuracy']==.5


def test_boundaries_and_logbar_information_limit():
    assert exp.actual_band(200,(200,20_000))==0
    assert exp.actual_band(201,(200,20_000))==1
    assert exp.actual_band(20_000,(200,20_000))==1
    assert exp.actual_band(20_001,(200,20_000))==2
    from nanojev_multiscale_activation_decoder import grid
    lower_bound=exp.collision_feasibility(grid())
    assert lower_bound['codes']==239
    assert lower_bound['infeasible_5pct_groups']==0


def test_fit_returns_oracle_and_predicted_routes_with_finite_errors():
    y=np.array([-10_000.,-1000.,-200.,-10.,-1.,0.,1.,10.,200.,1000.,10_000.])
    rng=np.random.default_rng(5)
    # Synthetic hidden states contain sign, magnitude, plus unrelated components.
    x=np.column_stack([np.sign(y)*6.,np.log1p(abs(y))*5.,abs(y)/1000.,
                       np.arange(len(y))*.01,rng.normal(size=len(y)),
                       np.eye(len(y))*.3])
    fact=exp.HeadFactory(x,4.)
    for bounds in ((200,),(200,2_000)):
        results=exp.fit_hierarchy(x,y,bounds,4.,factory=fact)
        assert len(results['predicted'])==len(y)
        assert np.isfinite(results['predicted']).all()
        assert np.isfinite(results['oracle']).all()
        assert set(results['heads']) >= {'zero','sign','route_above_200'}
        assert len(results['heads']) == len(bounds)+2+2*(len(bounds)+1)
        assert 0 <= results['total_effective_df']
        report,_,_=exp.metrics(y,results['predicted'],bounds)
        assert 0<=report['within_5pct_rate']<=1


def test_binary_heads_learn_perfectly_separated_sign_and_zero():
    y=np.array([-5.,-2.,-1.,0.,1.,2.,5.])
    # Each value has its own activation, so in-sample detection should be possible.
    x=np.eye(len(y)) * 30.
    results=exp.fit_hierarchy(x,y,(2,),5.)
    assert results['zero_pred'][3]
    assert not results['zero_pred'][0]
    assert np.array_equal(results['sign_pred'][y!=0],np.sign(y[y!=0]))


def test_run_requires_capture_and_does_not_modify_source(tmp_path):
    with pytest.raises(FileNotFoundError,match='MISSING_CAPTURE'):
        exp.run(tmp_path/'missing',tmp_path/'out')
    with pytest.raises(ValueError,match='MUST_DIFFER'):
        exp.run(tmp_path,tmp_path)


def test_end_to_end_reads_saved_records_and_writes_reports(tmp_path, capsys):
    import nanojev_multiscale_activation_decoder as base
    import nanojev_decimal_number_activation_map as dec
    src=tmp_path/'capture'; dst=tmp_path/'results'
    (src/'records').mkdir(parents=True)
    values=[-1000,-200,-10,-1,0,1,10,200,1000]
    identity={'schema':'mock-model','model':'mock'}
    dec.write_json(src/'manifest.json',{'values':values,'fractional_digits':1})
    dec.write_json(src/'model_identity.json',identity)
    rng=np.random.default_rng(6)
    code_vectors={}
    for n in values:
        q=base.quantize(n)
        for kind,key in (('decimal',n),('logbar',q),('paired',n)):
            if (kind,key) in code_vectors: continue
            raw_n=n if kind!='logbar' else np.sign(q)*np.expm1(abs(q)/10.)
            values_signal=np.array([np.sign(raw_n)*5., np.log1p(abs(raw_n))*4.,
                                    np.sign(raw_n)*np.log1p(abs(raw_n))*2.,
                                    (1 if raw_n==0 else -1)*5.,raw_n/1000.])
            vectors=np.zeros((2,5 if kind=='paired' else 2,len(values_signal)),dtype=np.float32)
            vectors[:,1,:]=values_signal+rng.normal(0,.0001,size=len(values_signal))
            if kind=='paired': vectors[:,4,:]=values_signal
            description=base.prompt(kind,key)
            dec.save_record(base.record_path(src,kind,key),
                            {'prompt':description,'prompt_sha256':dec.digest(description),
                             'model_identity':identity,'shape':list(vectors.shape)},vectors)
            code_vectors[kind,key]=True
    result=exp.run(src,dst,layers=(1,),df=4.)
    assert len(result['stages'])==3*len(exp.REGIMES) or len(result['stages'])>=3
    assert set(result['final_by_representation_layer'])=={'decimal_layer_1','logbar_layer_1','paired_layer_1'}
    assert len(result['baseline_by_representation_layer'])==3
    assert (dst/'hierarchy_predictions.csv').is_file()
    assert (dst/'hierarchy_heads.csv').is_file()
    assert (dst/'hierarchy_models.npz').is_file()
    assert (dst/'hierarchy_summary.json').is_file()
    for entry in result['stages']:
        assert entry['total_effective_df']>=0
        assert 'within_5pct_rate' in entry['oracle']
        assert 'within_5pct_rate' in entry['predicted']
        assert entry['predicted']['count']==len(values)
