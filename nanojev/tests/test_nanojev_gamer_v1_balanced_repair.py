"""Disposable balance/order lab: exact split discipline, independent zero gates."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_balanced_repair as repair
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_staircase as stairs


def test_cli_registered_and_flags_restricted():
    p=stairs.parser()
    a=p.parse_args(['head-100-balanced-repair','--run','r','--repair-epochs','12',
                     '--repair-no-full100'])
    assert a.repair_epochs==12 and a.repair_no_full100
    with pytest.raises(ValueError,match='BALANCED_REPAIR_FLAGS_ONLY'):
        stairs.main(['status','--run','r','--repair-epochs','12'])
    with pytest.raises(ValueError,match='EXPERIMENT_NO_STEPS'):
        stairs.main(['head-100-balanced-repair','--run','r','--steps','3'])


def test_balanced_batches_are_class_equal_with_nearzero_emphasis_and_fit_only():
    y=np.array([-3.,-2.,-.5,-.25,0.,0.,0.,0.,.25,.5,2.,3.,99.])
    ids=np.arange(12,dtype=int)
    a=list(repair._balanced_batches(y,ids,batch_size=30,steps=4,seed=123))
    b=list(repair._balanced_batches(y,ids,batch_size=30,steps=4,seed=123))
    assert all(np.array_equal(x,z) for x,z in zip(a,b))
    for batch in a:
        assert len(batch)==30 and np.all(batch<12)
        assert np.bincount(suff._classes(y[batch]),minlength=3).tolist()==[10,10,10]
        assert int(np.sum((y[batch]<0)&(y[batch]>=-1)))>=5
        assert int(np.sum((y[batch]>0)&(y[batch]<=1)))>=5
    with pytest.raises(ValueError,match='MISSING_SIGN_OR_ZERO'):
        list(repair._balanced_batches(y,np.arange(8),batch_size=12,steps=1,seed=1))


def test_objective_orders_cross_game_nonzero_magnitudes_and_backpropagates():
    torch=pytest.importorskip('torch')
    labels=np.array([-3.,-1.,0.,0.,1.,3.],dtype=np.float32)
    games=np.array(['a','b','c','d','e','f'])
    c=torch.tensor(suff._classes(labels))
    logits=torch.full((len(labels),3),-1.,requires_grad=True)
    predlog=torch.tensor([.1,.3,0.,0.,.3,.1],requires_grad=True)
    # Ranking is deliberately backwards for far-apart magnitudes.
    loss,components=repair._objective(torch,logits,predlog,labels,games)
    assert components['ranking_pairs']>0
    assert components['ordering']>0
    loss.backward()
    assert torch.isfinite(logits.grad).all() and torch.isfinite(predlog.grad).all()


def test_magnitude_holdout_not_used_for_training_or_validation():
    torch=pytest.importorskip('torch')
    n=96
    y=np.tile([-1.5,-.5,0.,.5,1.5,3.],n//6).astype(np.float32)
    games=np.repeat([f'g{i:02d}' for i in range(16)],6)
    features=np.zeros((n,4*8),dtype=np.float32)
    fit=np.arange(0,66)
    val=np.arange(66,90)
    # The reserved 1..2 band must be removed by the caller, and this helper
    # must refuse to train if it is accidentally reintroduced.
    with pytest.raises(ValueError,match='RESERVED_MAGNITUDE_LEAK'):
        repair._fit(torch,features,y,games,fit,val,[2],device='cpu',epochs=2,
             batch_size=12,hidden=8,lr=.001,seed=3,vectors=4,width=8)
    filt_fit=np.asarray([i for i in fit if not suff._reserved_numeric_range(y[i])])
    filt_val=np.asarray([i for i in val if not suff._reserved_numeric_range(y[i])])
    net,meta,_=repair._fit(torch,features,y,games,filt_fit,filt_val,[2],
           device='cpu',epochs=2,batch_size=12,hidden=8,lr=.001,seed=3,vectors=4,width=8)
    assert meta['sampling_last_epoch']['class_draws']==[sum(meta['sampling_last_epoch']['class_draws'])//3]*3
    assert meta['ranking_pairs_total']>=0
    assert meta['parameters']<10000
    with pytest.raises(ValueError,match='FIT_VALIDATION_LEAK'):
        repair._fit(torch,features,y,games,filt_fit,filt_fit,[2],
           device='cpu',epochs=2,batch_size=12,hidden=8,lr=.001,seed=3,vectors=4,width=8)




def test_float32_boundary_guard_preserves_original_holdout_and_excludes_rounded_values():
    near_one=math.nextafter(1.,0.)
    near_sixteen=16.-1e-7
    assert not suff._reserved_numeric_range(near_one)
    assert not suff._reserved_numeric_range(near_sixteen)
    assert float(np.float32(near_one))==1.0
    assert float(np.float32(near_sixteen))==16.0
    assert repair._reserved_for_fitting(near_one)
    assert repair._reserved_for_fitting(near_sixteen)
    assert repair._reserved_for_fitting(1.5)
    assert not repair._reserved_for_fitting(3.)
    assert not repair._reserved_for_fitting(-.5)
    assert repair._reserved_for_fitting(math.nextafter(0.,1.))
    games=np.asarray(['fit','fit','fit','fit','fit','val','val','val','val','val'])
    labels=np.asarray([-3.,0.,3.,near_one,near_sixteen,-3.,0.,3.,near_one,near_sixteen],dtype=np.float64)
    fold={'fold':0,'fit_games':['fit'],'val_games':['val'],
          'fit':np.arange(5),'val':np.arange(5,10),
          'test':np.asarray([100,101]),'test_games':['never_seen']}
    clean=repair._filter_rounded_magnitude_boundaries(fold,labels)
    assert clean['fit'].tolist()==[0,1,2]
    assert clean['val'].tolist()==[5,6,7]
    assert clean['float32_boundary_exclusions']=={'fit':[3,4],'val':[8,9]}
    assert fold['fit'].tolist()==[0,1,2,3,4]  # No mutation of original split
    assert clean['test'].tolist()==[100,101]
    assert clean['test_games']==['never_seen']
    strat=repair._ensure_validation_classes(clean,labels,games)
    assert strat['float32_boundary_exclusions']==clean['float32_boundary_exclusions']
    assert set(strat['fit']).isdisjoint(set(strat['val']))
    assert all(not repair._reserved_for_fitting(labels[i]) for i in strat['fit'])
    assert all(not repair._reserved_for_fitting(labels[i]) for i in strat['val'])


def test_float32_boundary_is_rejected_by_trainer_even_if_float64_split_admitted_it():
    torch=pytest.importorskip('torch')
    borderline=math.nextafter(1.,0.)
    y=np.asarray([-3.,0.,3.,borderline,-3.,0.,3.],dtype=np.float64)
    games=np.asarray(['fit']*4+['val']*3)
    features=np.zeros((len(y),4*8),dtype=np.float32)
    with pytest.raises(ValueError,match='RESERVED_MAGNITUDE_LEAK'):
        repair._fit(torch,features,y,games,np.arange(4),np.arange(4,7),[2],
           device='cpu',epochs=1,batch_size=12,hidden=8,lr=.001,seed=4,vectors=4,width=8)
    net,meta,_=repair._fit(torch,features,y,games,np.arange(3),np.arange(4,7),[2],
         device='cpu',epochs=1,batch_size=12,hidden=8,lr=.001,seed=4,vectors=4,width=8)
    assert meta['fit_class_counts']==[1,1,1]
    assert meta['validation_class_counts']==[1,1,1]


def test_deterministic_train_only_validation_stratification_preserves_holdout():
    games=np.repeat(['neg_a','neg_b','positive','zero','balanced'],3)
    labels=np.asarray([-2,-1,-.5,-4,-3,-.5,2,3,4,0,0,0,-1,0,1.],dtype=float)
    f={'fold':0,'fit_games':['neg_a','neg_b','zero','balanced'],
       'val_games':['positive'], 'fit':np.asarray([0,1,2,3,4,5,9,10,11,12,13,14]),
       'val':np.asarray([6,7,8]),'test':np.asarray([40,41]),'test_games':['heldout']}
    adjusted=repair._ensure_validation_classes(f,labels,games)
    assert len(adjusted['validation_class_swaps'])==1
    assert set(adjusted['fit_games']).isdisjoint(adjusted['val_games'])
    assert set(adjusted['fit']).isdisjoint(adjusted['val'])
    assert adjusted['test'].tolist()==[40,41] and adjusted['test_games']==['heldout']
    assert (np.bincount(suff._classes(labels[adjusted['fit']]),minlength=3)>0).all()
    assert (np.bincount(suff._classes(labels[adjusted['val']]),minlength=3)>0).all()
    assert adjusted=={**adjusted,'validation_class_swaps':adjusted['validation_class_swaps']}


def test_pairing_excludes_validation_only_and_retains_unseen_all():
    dev=[{'game_id':'a','answer':0.},{'game_id':'b','answer':-1.},
         {'game_id':'c','answer':2.}]
    preds=[
       dict(arm='localized',index=0,fold=0,cohort='unseen',target=0.,prediction=0.,class_=1),
       dict(arm='localized',index=1,fold=0,cohort='unseen',target=-1.,prediction=1.,class_=2),
       dict(arm='localized',index=2,fold=0,cohort='unseen',target=2.,prediction=2.,class_=2),
       dict(arm='localized',index=0,fold=1,cohort='familiar',target=0.,prediction=0.,class_=1),
       dict(arm='localized',index=1,fold=1,cohort='familiar',target=-1.,prediction=-1.,class_=0),
    ]
    for p in preds:p['class']=p.pop('class_')
    paired,unmatched=repair._paired_audit(preds,dev,[0,1,2],'localized')
    assert unmatched==[2] and len(paired)==4
    scored,_=repair._score_rows(preds,[0,1,2],{'localized':[65]},dev,[])
    assert scored['localized']['counts']['all_unseen_cases']==3
    assert scored['localized']['counts']['unmatched_cases']==1
    assert scored['localized']['paired']['familiar']['all']['cases']==2
    assert scored['localized']['unseen_all']['unseen']['all']['cases']==3
    assert not scored['localized']['reserved_magnitude_gate']['passed']


def test_zero_gate_rejects_false_zero_and_reserved_nonzero_zero_preds():
    yy=np.asarray([-.5,-.25,-.1,0.,0.,.1,.25,.5,2.,3.])
    pp=np.asarray([0.,0.,0.,0.,0.,0.,0.,0.,0.,0.])
    m=repair._cell_metrics([{'target':float(a),'prediction':float(b),'class':1}
                            for a,b in zip(yy,pp)])
    assert m['zero_exact_rate']==1.
    assert m['zero_false_positive_rate']==1.
    assert not suff._zero_gate(m)['passed']


def test_actual_full_width_forward_and_gradients():
    torch=pytest.importorskip('torch')
    prior=torch.get_num_threads();torch.set_num_threads(1)
    try:
        f=np.zeros((12,102400),dtype=np.float32)
        y=np.tile([-3.,0.,3.],4).astype(np.float32)
        for i in range(len(y)):
            f[i,99*1024+720]=y[i]
        net=repair._model(torch,102400,8)
        xx=torch.from_numpy(suff._batch(f,[0,1,2,3,4,5],None))
        scores,mag=net(xx)
        loss,_=repair._objective(torch,scores,mag,y[:6],['a','b','c','d','e','f'])
        loss.backward()
        assert scores.shape==(6,3) and mag.shape==(6,)
        assert net.body[0].weight.grad is not None
    finally:torch.set_num_threads(prior)



def test_disposable_training_recovers_zero_and_small_negative_on_synthetic_frozen_features():
    torch=pytest.importorskip('torch')
    old=torch.get_num_threads();torch.set_num_threads(1)
    try:
        rng=np.random.default_rng(7)
        labels=np.tile([-3.,-2.,-.5,-.25,0.,.25,.5,2.,3.],20).astype(np.float32)
        games=np.repeat([f'g{i:02d}' for i in range(20)],9)
        feat=rng.normal(0,.035,(len(labels),8,12)).astype(np.float32)
        feat[:,5,0]=labels
        feat[:,5,1]=np.abs(labels)
        feat[:,5,2]=(labels==0).astype(float)*3
        feat=feat.reshape(len(labels),-1)
        fit=np.asarray([i for i in range(len(labels)) if games[i]<'g14'
                     and not suff._reserved_numeric_range(labels[i])])
        val=np.asarray([i for i in range(len(labels)) if 'g14'<=games[i]<'g17'
                     and not suff._reserved_numeric_range(labels[i])])
        held=np.asarray([i for i in range(len(labels)) if games[i]>='g17'])
        original=feat.copy()
        model,summary,_=repair._fit(torch,feat,labels,games,fit,val,[5],device='cpu',
            epochs=40,batch_size=30,hidden=24,lr=.003,seed=13,patience=15,
            vectors=8,width=12)
        pred,cls,_=suff._predict(torch,model,feat,held,[5],device='cpu',
                                  batch_size=64,vectors=8,width=12)
        m=suff._metrics(labels[held],pred,cls)
        assert m['zero_exact_rate']>.9
        assert m['small_negative_sign_rate']>.9
        assert m['zero_false_positive_rate']<.1
        assert m['nonzero_sign_rate']>.9
        assert summary['ranking_pairs_total']>0
        assert np.array_equal(original,feat)
    finally:torch.set_num_threads(old)


def test_full_run_frozen_inputs_and_four_arm_report(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    root=tmp_path/'generalization';exp=tmp_path/'expanded';source=tmp_path/'source'
    causal=tmp_path/'causal';capture=tmp_path/'capture'
    for p in (root,exp,source,causal,capture):p.mkdir()
    protected=[]
    for p in (root/'final_report.json',exp/'provenance.json',exp/'best_model.pt',
              capture/'activations.npz',source/'certified_pairs.jsonl',
              exp/'feature_cache'/'train.npz',exp/'feature_cache'/'dev.npz'):
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(b'FROZEN')
        protected.append(p)
    (exp/'provenance.json').write_text(json.dumps({'configuration':{'input_width':102400}}))
    before={str(p):suff._digest(p) for p in protected}
    games=[f'g{i:03d}' for i in range(32)]
    rows=[{'game_id':g,'answer':y,'prompt':f'{g}_{y}'}
          for g in games for y in (-.5,0.,.5,3.,math.nextafter(1.,0.))]
    features=np.zeros((len(rows),102400),dtype=np.float32)
    for i,r in enumerate(rows):features[i,65*1024]=r['answer']
    monkeypatch.setattr(repair,'_lineage',lambda _: (
       root,{'source_causal':str(causal), 'settings':{'seed':47,'folds':4},
         'arms':suff._arms([65,66,71,72],seed=47),
         'holdout':{'folds':[{'fold':f['fold'],'heldout_games':f['test_games'],
              'test_cases':len(f['test'])} for f in suff._split_groups(
                 rows,rows,games[:4],folds=4,seed=47)]}},
       {'checkpoint_sha256':'freeze'},
       {'contexts':[{'game_id':n} for n in games[:4]]},
       [65,66,71,72],exp,source,capture,protected))
    monkeypatch.setattr(repair.suff,'_cached_split',lambda e,s,k,sp:
                        (rows,features,exp/'feature_cache'/f'{sp}.npz'))
    monkeypatch.setattr(repair,'_fit',lambda *a,**k:(
           object(),{'best_epoch':1,'best_validation_loss':0.9,'parameters':2,
            'sampling_last_epoch':{'class_draws':[2,2,2]},'ranking_pairs_total':1},
           {'weight':torch.ones(2)}))
    def fake_predict(_torch,_net,x,ids,positions,**kwargs):
        ids=np.asarray(ids,dtype=int);y=np.asarray([rows[i]['answer'] for i in ids])
        cls=suff._classes(y);p=y.copy()
        probs=np.zeros((len(ids),3));probs[np.arange(len(ids)),cls]=1.
        return p,cls,probs
    monkeypatch.setattr(repair.suff,'_predict',fake_predict)
    report=repair.run_balanced_repair(root,device='cpu',epochs=5,batch_size=12,hidden=8)
    assert report['status']=='COMPLETE_DISPOSABLE_NOT_PROMOTED'
    assert len(report['results'])==4
    assert report['coverage']['certified_dev_cases']==28*5
    assert report['results']['localized']['counts']['all_unseen_cases']==28*5
    assert sum(row['fit_count']+row['validation_count'] for row in report['float32_boundary_exclusions'])>0
    out=Path(str(root)+'-balanced-repair-0001')
    assert (out/'final_report.json').is_file()
    assert (out/'predictions.csv').is_file()
    assert len(list((out/'experimental_models').glob('*.pt')))==16
    assert all(suff._digest(p)==sha for p,sha in before.items())
    assert report['promotion'].startswith('BLOCKED_')
