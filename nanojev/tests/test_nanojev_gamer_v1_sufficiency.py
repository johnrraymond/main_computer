"""Disposable GAMER sufficiency: split, negative controls and zero gates."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_staircase as stairs


def test_phase_is_registered_and_rejects_foreign_flags():
    assert 'head-100-sufficiency' in stairs.parser()._option_string_actions['--help'].container._actions[1].choices
    args=stairs.parser().parse_args(['head-100-sufficiency','--run','r','--suff-epochs','16'])
    assert args.phase=='head-100-sufficiency' and args.suff_epochs==16
    with pytest.raises(ValueError,match='SUFFICIENCY_FLAGS_ONLY'):
        stairs.main(['status','--run','r','--suff-epochs','16'])


def test_fixed_magnitude_holdout_and_zero_retention():
    assert not suff._reserved_numeric_range(0)
    assert suff._reserved_numeric_range(1.5)
    assert suff._reserved_numeric_range(-1.5)
    assert not suff._reserved_numeric_range(.75)
    assert suff._reserved_numeric_range(16.2)
    assert not suff._reserved_numeric_range(8.2)


def test_no_game_leakage_calibration_games_excluded_and_value_reserved():
    names=[f'game_{i:03d}' for i in range(30)]
    train=[{'game_id':g,'answer':y} for g in names for y in (-2.,-.5,0.,.5,1.5,3.)]
    dev=[{'game_id':g,'answer':y} for g in names for y in (-.5,0.,1.5)]
    groups=suff._split_groups(train,dev,names[:3],folds=3,seed=47)
    scored=set()
    for group in groups:
        fit={train[i]['game_id'] for i in group['fit']}
        val={train[i]['game_id'] for i in group['val']}
        test={dev[i]['game_id'] for i in group['test']}
        assert not fit&val and not fit&test and not val&test
        assert not test.intersection(names[:3])
        assert all(not suff._reserved_numeric_range(train[i]['answer']) for i in group['fit'])
        assert all(not suff._reserved_numeric_range(train[i]['answer']) for i in group['val'])
        scored.update(test)
    assert scored==set(names[3:])


def test_false_zero_everywhere_fails_gate():
    labels=np.asarray([-1.,-.5,0.,0.,0.,.5,1.])
    p=np.zeros(len(labels));cl=np.full(len(labels),1)
    m=suff._metrics(labels,p,cl)
    assert m['zero_exact_rate']==1.0
    assert m['zero_false_positive_rate']==1.0
    assert m['nonzero_sign_rate']==0.0
    assert not suff._zero_gate(m)['passed']


def test_near_zero_negative_cannot_be_silently_classified_positive():
    y=np.asarray([-1.,-.5,-.25,0.,0.,.25,.5,1.])
    pred=np.asarray([1.,1.,1.,0.,0.,.25,.5,1.]); cls=suff._classes(pred)
    m=suff._metrics(y,pred,cls)
    assert m['small_negative_sign_rate']==0.0
    assert m['zero_exact_rate']==1.
    assert not suff._zero_gate(m)['passed']


def test_full_width_100_vectors_and_last_position_are_preserved():
    f=np.zeros((3,102400),dtype=np.float32)
    f[0,99*1024+720]=23
    f[1,65*1024+12]=-3
    got=suff._batch(f,[0,1],[65,99])
    assert got.shape==(2,2048)
    assert got[0,1024+720]==23
    assert got[1,12]==-3
    assert suff._batch(f,[2],None).shape==(1,102400)


def test_controls_are_disjoint_and_deterministic():
    a=suff._arms([65,66,71,72],seed=47)
    b=suff._arms([65,66,71,72],seed=47)
    assert a==b
    assert a['full100'] is None
    assert len(a['localized'])==len(a['neighbor_control'])==len(a['random_control'])==4
    assert not set(a['localized'])&set(a['neighbor_control'])
    assert not set(a['localized'])&set(a['random_control'])
    assert not set(a['neighbor_control'])&set(a['random_control'])


def test_small_readout_learns_sign_zero_and_magnitude_on_frozen_features():
    torch=pytest.importorskip('torch')
    rng=np.random.default_rng(6)
    vals=np.asarray([-3.,-2.,-.75,0.,.75,2.,3.]*16,dtype=np.float32)
    f=rng.normal(0,.03,(len(vals),8,12)).astype(np.float32)
    f[:,5,0]=vals;f[:,5,1]=np.abs(vals);f[:,5,2]=(vals==0).astype(np.float32)*3
    f=f.reshape(len(vals),-1)
    tr=np.arange(0,84);va=np.arange(84,98);te=np.arange(98,len(vals))
    net,report,_=suff._fit(torch,f,vals,tr,va,[5],device='cpu',epochs=65,
                 batch_size=28,hidden=24,lr=.004,seed=12,patience=12,vectors=8,width=12)
    p,c,_=suff._predict(torch,net,f,te,[5],device='cpu',batch_size=28,vectors=8,width=12)
    m=suff._metrics(vals[te],p,c)
    assert m['zero_recall']>.8
    assert m['nonzero_sign_rate']>.8
    assert report['parameters']<10000
    assert report['best_epoch']>=1


def test_zero_spelling_is_not_claimed_certified():
    torch=pytest.importorskip('torch')
    net=suff._model(torch,1024,12)
    # 4 formats have correctly shaped activations but no certified oracle.
    zeros={k:np.zeros((5,102400),dtype=np.float32) for k in ('0','+0','zero','-0')}
    result=suff._spelling_deck(torch,net,zeros,[0],device='cpu',batch_size=8)
    assert result['status']=='DESCRIPTIVE_SYNTHETIC_NO_CERTIFIED_ANSWER'
    assert result['classification_agreement_across_forms']==1.


def test_checkpoint_is_disposable_and_readable(tmp_path):
    torch=pytest.importorskip('torch')
    state={'weight':torch.arange(3)}
    dest=tmp_path/'isolated'/'experimental_models'/'fold01_localized.pt'
    suff._save_state(torch,dest,state,{'mode':'localized'})
    loaded=torch.load(dest,map_location='cpu',weights_only=True)
    assert loaded['schema']==suff.SCHEMA
    assert torch.equal(loaded['state_dict']['weight'],state['weight'])
    assert loaded['metadata']['mode']=='localized'


def test_sufficiency_command_end_to_end_with_disposable_artifacts(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    root=tmp_path/'causal';exp=tmp_path/'expanded';source=tmp_path/'source'
    capture=tmp_path/'capture';loc=tmp_path/'localization'
    paths=[root/'final_report.json',exp/'best_model.pt',exp/'provenance.json',
        capture/'final_report.json',capture/'activations.npz',
        loc/'final_report.json',source/'certified_pairs.jsonl',
        exp/'feature_cache'/'train.npz',exp/'feature_cache'/'dev.npz']
    for path in paths:
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'IMMUTABLE INPUT')
    before={str(path):suff._digest(path) for path in paths}
    games=[f'game{i:03d}' for i in range(30)]
    rows=[{'game_id':g,'answer':value,'prompt':f'{g}: {value}'}
          for g in games for value in (-.5,0.,.5)]
    # Full-width features: command must not inadvertently truncate to 256D.
    train_features=np.zeros((len(rows),102400),dtype=np.float32)
    dev_features=np.zeros_like(train_features)
    report={'checkpoint_sha256':'protected_head_hash',
            'source_localization':str(loc)}
    cr={'contexts':[{'game_id':name} for name in games[:3]],'checkpoint_step':500}
    monkeypatch.setattr(suff,'_source',lambda _: (root,report,cr,{},[65,66,71,72],exp,source,capture))
    monkeypatch.setattr(suff,'_cached_split',lambda e,s,ident,part: (
        rows,train_features if part=='train' else dev_features,
        exp/'feature_cache'/f'{part}.npz'))
    monkeypatch.setattr(suff,'_baseline_from_csv',lambda *args:(None,None))
    monkeypatch.setattr(suff,'_load_synthetic_zero_deck',lambda *args:None)
    monkeypatch.setattr(suff.gamer,'original_prompt',lambda r:r['prompt'])
    fake_metadata={'best_epoch':1,'epochs_executed':1,'best_validation_loss':1.,
        'parameters':10,'train_class_counts':[10,10,10]}
    monkeypatch.setattr(suff,'_fit',lambda *args,**kwargs:
         (object(),dict(fake_metadata),{'weight':torch.ones(2)}))
    def fake_predict(_torch,_net,_feat,ids,positions,**kwargs):
        indices=np.asarray(ids,dtype=int)
        cls=indices%3
        vals=np.choose(cls,[-.5,0.,.5])
        probs=np.zeros((len(ids),3));probs[np.arange(len(ids)),cls]=1.
        return vals,cls,probs
    monkeypatch.setattr(suff,'_predict',fake_predict)
    monkeypatch.setattr(suff,'_model',lambda *args,**kwargs:None)
    identity={'configuration':{'input_width':102400}}
    (exp/'provenance.json').write_text(json.dumps(identity),encoding='utf-8')
    before[str(exp/'provenance.json')]=suff._digest(exp/'provenance.json')
    result=suff.run_sufficiency(root,device='cpu',folds=3,epochs=5,batch_size=8,hidden=8)
    assert result['status']=='COMPLETE_EXPERIMENTAL_NOT_PROMOTED'
    assert result['holdout']['all_test_games_disjoint_from_training']
    assert result['certified_split_sizes']['scored_dev']==27*3
    assert result['results']['localized']['metrics']['cases']==27*3
    assert result['zero_spelling']['status']=='BLOCKED_SYNTHETIC_ZERO_CAPTURE_MISSING'
    output=Path(result['source_causal']).with_name(root.name+'-sufficiency-0001')
    assert (output/'final_report.json').is_file()
    assert (output/'heldout_predictions.csv').is_file()
    assert len(list((output/'experimental_models').glob('*.pt')))==12
    assert all(suff._digest(path)==sha for path,sha in before.items())


def test_full_102400_feature_arm_can_train_and_predict_with_frozen_cache():
    torch=pytest.importorskip('torch')
    prior_threads=torch.get_num_threads();torch.set_num_threads(1)
    try:
        labels=np.asarray([-1.,0.,1.]*12,dtype=np.float32)
        f=np.zeros((len(labels),102400),dtype=np.float32)
        f[:,65*1024]=labels
        f[:,99*1024+2]=2*labels
        copy=f.copy()
        net,summary,_=suff._fit(torch,f,labels,np.arange(24),np.arange(24,30),None,
            device='cpu',epochs=2,batch_size=8,hidden=8,lr=.001,seed=3,patience=2)
        pred,cls,probs=suff._predict(torch,net,f,np.arange(30,36),None,
            device='cpu',batch_size=6)
        assert pred.shape==(6,) and cls.shape==(6,) and probs.shape==(6,3)
        assert np.isfinite(pred).all()
        assert summary['parameters']>800_000
        assert np.array_equal(f,copy)
    finally:
        torch.set_num_threads(prior_threads)
