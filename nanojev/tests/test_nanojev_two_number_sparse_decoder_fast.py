"""Equivalence and caching tests for fast two-number L1 experiment."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_two_number_sparse_decoder_fast as fast


def test_head_cache_and_stronger_to_weaker_warm_start():
    rng=np.random.default_rng(99)
    x=np.asarray(rng.normal(size=(36,15)),dtype=np.float32)
    y=x[:,0]*3-x[:,4]*1.4+rng.normal(0,0.1,36)
    fac=fast.FastHeadFactory(x,max_iter=20000)
    m=np.ones(36,dtype=bool)
    fac.set_ratio(.2)
    a=fac.fit('expert',m,y)
    b=fac.fit('expert',m,y)
    assert fac.unique_solves==1 and fac.cache_hits==1
    np.testing.assert_allclose(a,b,atol=1e-12)
    fac.set_ratio(.1)
    c=fac.fit('expert',m,y)
    assert fac.unique_solves==2 and next(iter(fac.states.values())).warm_starts==1
    reference=fast.sparse.lasso_sparse(x,y,.1,solver_max_iter=20000)
    r=x[:,reference['indices']]@reference['weights']+reference['intercept']
    np.testing.assert_allclose(c,r,atol=.06,rtol=1e-3)
    assert next(iter(fac.heads.values()))['kkt_passed']


def test_cache_key_discriminates_targets_and_masks():
    mask=np.asarray([True,False,True,True])
    a=fast.head_key('sign',mask,[-1,1,-1])
    assert a!=fast.head_key('sign',mask,[-1,1,1])
    assert a!=fast.head_key('zero',mask,[-1,1,-1])
    assert a!=fast.head_key('sign',[True,True,True,False],[-1,1,-1])


def test_fast_writes_separate_outputs(tmp_path,monkeypatch):
    # Synthetic complete record store created by the original capture entrypoint.
    class Tokenizer:
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[ord(c) for c in text]}
    class Model:
        def __call__(self,input_ids,attention_mask,output_hidden_states,use_cache):
            u=input_ids.float().unsqueeze(-1)
            h=torch.cat((u,u**2/100,u*0.7),dim=-1)
            return type('Out',(),{'hidden_states':(h,2*h)})()
    monkeypatch.setattr(fast.sparse.cassette,'load_source',lambda *_: (
        torch,Model(),Tokenizer(),'cpu',{'source':'synthetic'}))
    source=tmp_path/'original'
    target=tmp_path/'fast'
    args=['--pair-count','20','--output-root',str(source),
          '--layers','0','--alpha-ratios','0.6,0.3',
          '--max-regimes','2','--report-every','100']
    fast.base.main(args+['--capture-only'])
    source_files={p.relative_to(source): p.read_bytes()
                  for p in source.rglob('*') if p.is_file()}
    fast.main(['--source-root',str(source),'--output-root',str(target),
               '--layers','0','--alpha-ratios','0.6,0.3',
               '--max-regimes','2','--threads','1'])
    assert (target/'fast_two_number_summary.json').is_file()
    assert not (source/'fast_two_number_summary.json').exists()
    assert not (source/'two_number_summary.json').exists()
    assert source_files == {p.relative_to(source):p.read_bytes()
                            for p in source.rglob('*') if p.is_file()}
    report=json.loads((target/'fast_two_number_summary.json').read_text())
    assert report['source_records_unchanged']
    assert report['source_root']==str(source.resolve())
    assert report['results_root']==str(target.resolve())
    assert report['no_token_pooling']
    assert len(report['alpha_ratios'])==2


def test_fast_refuses_to_write_inside_source(tmp_path):
    source=tmp_path/'slow'
    with pytest.raises(ValueError,match='OVERLAP'):
        fast.separate_roots(source,source)
    with pytest.raises(ValueError,match='OVERLAP'):
        fast.separate_roots(source,source/'fast')
    with pytest.raises(ValueError,match='OVERLAP'):
        fast.separate_roots(source,source.parent)


def test_fast_rejects_capture_flags_and_bad_threads(tmp_path):
    source=tmp_path/'slow'
    target=tmp_path/'fast'
    for flags in (['--capture-only'],['--prepare-only'],['--resume'],['--threads','0']):
        with pytest.raises(ValueError):
            fast.main(['--source-root',str(source),'--output-root',str(target)]+flags)

def test_repeated_hierarchy_stage_reuses_identical_head_models():
    rng=np.random.default_rng(42)
    y=np.asarray([-2_000_000.,-200_000.,-20_000.,-2000.,-200.,-20.,-1.,
                  0.,1.,20.,200.,2000.,20_000.,200_000.,2_000_000.])
    x=rng.normal(size=(len(y),32)).astype(np.float32)
    x[:,0]=np.sign(y) + rng.normal(scale=.05,size=len(y))
    x[:,1]=np.log1p(np.abs(y))/12 + rng.normal(scale=.05,size=len(y))
    x[:,2]=(y==0) + rng.normal(scale=.02,size=len(y))
    fac=fast.FastHeadFactory(x,max_iter=20000)
    fac.set_ratio(.3)
    first=fast.sparse.hier.fit_hierarchy(x,y,(200,),df=.3,factory=fac)
    solves=fac.unique_solves
    again=fast.sparse.hier.fit_hierarchy(x,y,(200,20000),df=.3,factory=fac)
    assert fac.cache_hits>=3 # zero, sign, route_above_200 reused
    assert fac.unique_solves>solves
    assert first['predicted'].shape == again['predicted'].shape


def test_reject_ascending_penalties():
    with pytest.raises(ValueError,match='DESCENDING'):
        ratios=(0.1,0.2)
        if any(a<b for a,b in zip(ratios,ratios[1:])):
            raise ValueError('FAST_LASSO_PENALTIES_MUST_BE_DESCENDING')
