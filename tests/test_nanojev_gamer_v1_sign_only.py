"""GAMER disposable sign-only probe; frozen upstream, disjoint games & magnitudes."""
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_sign_only as sign
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_staircase as staircase


def test_original_split_filters_zero_and_preserves_reserved_band():
    labels=np.array([-1.01,-.9,0.,1.01,.9,2.01,-2.01,1.,-1.,3.01])
    games=np.array(['a','a','a','a','a','a','a','a','a','b'])
    fold={'fit':np.array([0,1,2,3,4,5,6]),'val':np.array([7,8,9]),
          'test_games':['c']}
    # The full original split already excludes the exact reserved 1.0 values;
    # arbitrary user-made splits containing them MUST be rejected instead.
    with pytest.raises(ValueError,match='RESERVED'):
        sign._nonzero_partition(fold,labels,games)
    fold={'fit':np.array([0,1,2,3,4,5,6]),'val':np.array([0,1]),'test_games':['c']}
    with pytest.raises(ValueError,match='RESERVED|INDEX_LEAK|GAME_LEAK'):
        sign._nonzero_partition(fold,labels,games)


def test_game_disjoint_nonzero_partition():
    labels=np.array([-0.5,0.,.5, -3., 3., 0., -.75, .75])
    games=np.array(['fit']*3+['val']*3+['test']*2)
    fold={'fit':np.array([0,1,2]),'val':np.array([3,4,5]),'test_games':['test']}
    result=sign._nonzero_partition(fold,labels,games)
    assert result['fit'].tolist()==[0,2]
    assert result['val'].tolist()==[3,4]
    fold['test_games']=['val']
    with pytest.raises(ValueError,match='GAME_LEAK'):
        sign._nonzero_partition(fold,labels,games)


def test_sign_sampler_equal_classes_no_zeros():
    y=np.array([-0.1,-2,-.5,-6,0.,.2,.7,8.,5.])
    ids=np.array([0,1,2,3,5,6,7,8])
    batches=list(sign._sample_sign_balanced(y,ids,steps=4,batch_size=12,seed=2))
    assert len(batches)==4
    for b in batches:
        assert (y[b]<0).sum()==6
        assert (y[b]>0).sum()==6
        assert not (y[b]==0).any()
        assert set(b).issubset(set(ids))
    assert np.array_equal(batches[0],next(sign._sample_sign_balanced(y,ids,steps=1,batch_size=12,seed=2)))
    with pytest.raises(ValueError,match='ZERO'):
        list(sign._sample_sign_balanced(y,np.arange(9),steps=1,batch_size=12,seed=2))


def test_binary_sign_only_fit_with_real_gradients():
    torch=pytest.importorskip('torch')
    rng=np.random.default_rng(7)
    n=64
    x=rng.normal(scale=.12,size=(n,8*4)).astype(np.float32)
    y=np.where(np.arange(n)%2,-.5,.5)
    x[:,0]=np.where(y<0,-6.,6.)
    fold={'fit':np.arange(0,40),'val':np.arange(40,56)}
    net,meta,state=sign._fit(torch,x,y,fold,positions=[0,1],device='cpu',
                  epochs=20,batch_size=8,hidden=12,lr=.002,patience=5,seed=47,
                  vectors=8,width=4)
    prob=sign._predict(torch,net,x,np.arange(56,64),[0,1],device='cpu',
                       batch_size=4,vectors=8,width=4)
    assert prob.shape==(8,2)
    assert (prob[:,1]>=.5).tolist()==(y[56:]>0).tolist()
    assert meta['best_epoch']>=1
    assert set(state)==set(net.state_dict())
    with pytest.raises(ValueError,match='NUMERIC_LEAK'):
        sign._fit(torch,x,np.where(np.arange(n)==3,0.,y),fold,[0,1],device='cpu',
                  epochs=1,batch_size=8,hidden=12,lr=.002,patience=1,seed=2,
                  vectors=8,width=4)


def test_sign_scores_threshold_free_and_reserved_band():
    rows=[{'arm':'localized','fold':0,'index':i,'game_id':'g'+str(i//2),'target':y,'p_positive':p}
          for i,(y,p) in enumerate([(-.25,.1),(.25,.9),(-1.,.8),(1.,.2),(-2.,.2),(2.,.8)])]
    metrics=sign._segment(rows)
    assert metrics['all']['cases']==6
    assert metrics['all']['negative_cases']==3
    assert metrics['all']['auc']>0.5
    assert metrics['eligible_magnitudes']['cases']==4  # -1 and 1 reserved
    assert metrics['reserved_untrained_magnitudes']['cases']==2
    with pytest.raises(ValueError,match='NONZERO'):
        sign._metrics([{'target':0.,'p_positive':.5}])
    results,csvrows=sign._evaluate_predictions(rows,{'localized':[0,1]},set(range(6)))
    assert len(csvrows)==6
    assert results['localized']['all']['cases']==6
    with pytest.raises(ValueError,match='UNPAIRED'):
        sign._evaluate_predictions(rows[:-1],{'localized':[0,1]},set(range(6)))


def test_cli_phase_and_no_steps():
    parsed=staircase.parser().parse_args(['head-100-sign-only','--run','some-path','--device','cpu'])
    assert parsed.sign_epochs==35
    assert parsed.sign_output is None
    with pytest.raises(ValueError,match='NO_STEPS'):
        staircase.main(['head-100-sign-only','--run','unused','--steps','1'])
    with pytest.raises(ValueError,match='FLAGS_ONLY'):
        staircase.main(['head-100-separability','--run','unused','--sign-epochs','5'])


def test_end_to_end_disposable_source_immutability(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    folder=tmp_path/'lineage';folder.mkdir()
    source=tmp_path/'original-source';source.mkdir()
    exp=tmp_path/'expanded';exp.mkdir()
    capture=tmp_path/'capture';capture.mkdir()
    original=source/'best_model.pt';original.write_bytes(b'production never changes')
    (exp/'best_model.pt').write_bytes(b'head never changes')
    (exp/'provenance.json').write_text(json.dumps({'configuration':{'input_width':102400}}))
    (source/'certified_pairs.jsonl').write_text('[]')
    checkpoint_hash=suff._digest(original)
    source_balanced={'source_generalization_or_sufficiency':str(folder),
            'source_expanded':str(exp),'selected_positions':[65,66,71,72],
            'settings':{'original_split_seed':47},
            'arms':suff._arms([65,66,71,72],seed=47)}
    monkeypatch.setattr(sign,'_lineage',lambda root:(source_balanced,{}, {}, [65,66,71,72], exp, source, capture,[original]))
    train=[]
    for game in ('f1','f2','v1','v2'):
        for val in (-.5,.5):train.append({'game_id':game,'answer':val})
    dev=[]
    for game in ('t1','t2','t3','t4'):
        for val in (-.5,0.,.5,1.0):dev.append({'game_id':game,'answer':val})
    rng=np.random.default_rng(2)
    xtrain=rng.normal(size=(len(train),102400)).astype(np.float32)
    xdev=rng.normal(size=(len(dev),102400)).astype(np.float32)
    xdev[:,0]=np.where(np.asarray([r['answer'] for r in dev])<0,-3,3)
    def cache(exp_,src,identity,split):
        return (train,xtrain,original) if split=='train' else (dev,xdev,original)
    monkeypatch.setattr(suff,'_cached_split',cache)
    monkeypatch.setattr(sign.cal,'_folds',lambda *args:[
      {'fold':i,'fit':np.array([0,1,2,3]),'val':np.array([4,5,6,7]),
       'test':np.array([i*4+j for j in range(4)]),'test_games':['t'+str(i+1)]}
      for i in range(4)])
    def fake_fit(torch_,x,y,fold,positions,**kwargs):
        return None,{'best_epoch':1,'epochs_executed':1}, {'probe':torch_.tensor([1.])}
    monkeypatch.setattr(sign,'_fit',fake_fit)
    def fake_predict(torch_,net,features,ids,positions,**kwargs):
        positive=np.where(features[ids,0]<0,.12,.88)
        return np.stack([1-positive,positive],axis=1)
    monkeypatch.setattr(sign,'_predict',fake_predict)
    out=tmp_path/'new-sign-experiment'
    record=sign.run_sign_only(folder,output=out,device='cpu',epochs=5,batch_size=8)
    assert record['status']=='COMPLETE_DISPOSABLE_SIGN_ONLY_NOT_PROMOTED'
    assert record['coverage']['dev_nonzero_cases']==12
    assert record['coverage']['dev_zero_cases_excluded']==4
    assert record['coverage']['nonzero_reserved_cases']==4
    assert record['results']['localized']['all']['cases']==12
    assert (out/'heldout_predictions.csv').is_file()
    checkpoints=list((out/'experimental_models').glob('*.pt'))
    assert len(checkpoints)==16
    assert torch.load(checkpoints[0],map_location='cpu',weights_only=True)['schema']==sign.SCHEMA
    assert suff._digest(original)==checkpoint_hash
    with pytest.raises(FileExistsError):
        sign.run_sign_only(folder,output=out,device='cpu',epochs=5,batch_size=8)
