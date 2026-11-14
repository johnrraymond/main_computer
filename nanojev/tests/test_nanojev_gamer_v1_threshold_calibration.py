"""Read-only GAMER post-hoc zero/sign calibration: validation-only integrity."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_threshold_calibration as cal
from tools import nanojev_gamer_v1_balanced_repair as repair
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_staircase as stairs


def test_registered_cli_and_no_train_steps():
    a=stairs.parser().parse_args(['head-100-calibration','--run','lab','--cal-batch','24'])
    assert a.cal_batch==24
    with pytest.raises(ValueError,match='CALIBRATION_FLAGS_ONLY'):
        stairs.main(['status','--run','lab','--cal-batch','24'])
    with pytest.raises(ValueError,match='READ_ONLY_NO_STEPS'):
        stairs.main(['head-100-calibration','--run','lab','--steps','100'])


def test_raw_argmax_is_exact_zero_threshold_origin_and_sign_zero_semantics():
    logits=np.asarray([[-1.,2.,0.],[2.,.5,-1.],[-1.,.5,2.],
                       [1.,1.,0.],[.2,-.5,.2]])
    assert np.array_equal(cal._decide(logits,0.,0.),np.argmax(logits,axis=1))
    c=cal._decide(logits,1.,-.1)
    assert c.tolist()==[1,0,2,0,0]
    magnitudes=np.asarray([-2.,.2,.2,.2,.2]);p=cal._predictions(c,magnitudes)
    assert p[0]==0. and p[1]<0 and p[2]>0
    assert np.all(cal._softmax(logits)>0) and np.allclose(cal._softmax(logits).sum(1),1.)


def test_thresholds_trained_only_from_validation_and_deterministic():
    labels=np.tile([-2.,0.,2.],15)
    rng=np.random.default_rng(3)
    logits=np.stack([-labels, np.where(labels==0,2.5,0.9),labels],axis=1)
    logits=np.asarray(logits,dtype=float)+rng.normal(0,.1,(len(labels),3))
    # Early threshold can be evaluated, but changing HELDOUT labels cannot change it.
    a=cal._calibrate(logits,labels)
    b=cal._calibrate(logits.copy(),labels.copy())
    assert a==b
    assert a['validation']['class_counts']==[15,15,15]
    assert a['validation']['selected_objective'][0]<=a['validation']['raw_objective'][0]+1e-12
    assert a['validation']['candidate_grid']['total']>50
    assert a['validation']['raw_argmax']['zero_recall']>=0.


def test_imbalanced_probabilities_descriptive_not_selection():
    logits=np.asarray([[5.,0.,0.]]*15+[[0.,5.,0.]]*5+[[0.,0.,5.]]*10)
    labels=np.asarray([-1.]*15+[0.]*5+[1.]*10)
    a=cal._reliability(logits,labels)
    assert a['cases']==30 and a['class_counts']==[15,5,10]
    assert a['brier_three_class']<.01
    assert len(a['confidence_bins'])>=1
    assert len(a['mean_softmax_probabilities'])==3
    b=cal._reliability(logits,np.asarray([1.]*15+[0.]*5+[-1.]*10))
    assert b['brier_three_class']>a['brier_three_class']


def test_reserved_magnitude_zero_false_positives_cannot_pass():
    original=[{'target':a, 'raw_prediction':0.,'calibrated_prediction':0.,
               'raw_class':1,'calibrated_class':1,
               'negative_probability':.1,'zero_probability':.8,'positive_probability':.1}
              for a in (-1.5,1.5,16.,-16.)]
    scored=cal._score(original)
    assert scored['threshold_calibrated']['zero_false_positive_rate']==1.
    assert scored['raw']['zero_cases']==0
    assert scored['delta']['zero_precision']==0.


def test_checkpoint_provenance_fingerprint_enforced(tmp_path):
    torch=pytest.importorskip('torch')
    net=suff._model(torch,1024,8)
    path=tmp_path/'saved.pt'
    suff._save_state(torch,path,{k:v.detach().cpu() for k,v in net.state_dict().items()},
        {'experiment_schema':repair.SCHEMA,'fold':0,'mode':'localized',
         'positions':[5],'source_checkpoint_sha256':'origin'})
    result=cal._load_checkpoint(torch,path,'localized',0,[5],{'hidden':8},'origin',device='cpu')
    assert result is not None
    with pytest.raises(ValueError,match='PROVENANCE_MISMATCH'):
        cal._load_checkpoint(torch,path,'localized',1,[5],{'hidden':8},'origin',device='cpu')
    with pytest.raises(ValueError,match='PROVENANCE_MISMATCH'):
        cal._load_checkpoint(torch,path,'localized',0,[4],{'hidden':8},'origin',device='cpu')


def test_validation_folds_reconstruction_refuses_changed_split(monkeypatch):
    train=[{'game_id':'fit','answer':v} for v in (-3.,0.,3.)]+[
           {'game_id':'val','answer':v} for v in (-3.,0.,3.)]
    dev=[{'game_id':'test','answer':0.}]
    fold={'fold':0,'fit':np.arange(3),'val':np.arange(3,6),'test':np.array([0]),
          'fit_games':['fit'],'val_games':['val'],'test_games':['test']}
    monkeypatch.setattr(cal.suff,'_split_groups',lambda *a,**k:[fold])
    prior={'settings':{'folds':1,'seed':47},'holdout':{'folds':[{'heldout_games':['test'],'test_cases':1}]}}
    balanced={'settings':{'folds':1,'original_split_seed':47},
              'float32_boundary_exclusions':[{'fold':0,'fit_indices':[],'validation_indices':[]}],
              'validation_stratification':[{'fold':0,'swaps':[]}]}
    good=cal._folds(train,dev,{'contexts':[]},prior,balanced)
    assert good[0]['val'].tolist()==[3,4,5]
    balanced['validation_stratification'][0]['swaps']=[{'bad':'change'}]
    with pytest.raises(ValueError,match='PARTITION_CHANGED'):
        cal._folds(train,dev,{'contexts':[]},prior,balanced)


def test_actual_readonly_end_to_end_two_folds(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    prev=torch.get_num_threads();torch.set_num_threads(1)
    try:
        root=tmp_path/'balanced';root.mkdir()
        parent=tmp_path/'generalization';parent.mkdir()
        exp=tmp_path/'expanded';exp.mkdir()
        source=tmp_path/'source';source.mkdir()
        capture=tmp_path/'capture';capture.mkdir()
        causal=tmp_path/'causal';causal.mkdir()
        (exp/'provenance.json').write_text(json.dumps({'configuration':{'input_width':102400}}))
        originals=[]
        for path in [exp/'provenance.json',exp/'best_model.pt',source/'certified_pairs.jsonl',
            exp/'feature_cache'/'train.npz',exp/'feature_cache'/'dev.npz']:
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():path.write_bytes(b'FROZEN')
            originals.append(path)
        before={str(p):suff._digest(p) for p in originals}
        vals=[-.5,0.,.5]
        train=[{'game_id':'fit','answer':v} for v in vals]+[
            {'game_id':'val','answer':v} for v in vals]
        dev=[{'game_id':g,'answer':v} for g in ('unseen_a','unseen_b') for v in vals]
        xf=np.zeros((len(train),102400),dtype=np.float32)
        xd=np.zeros((len(dev),102400),dtype=np.float32)
        for i,r in enumerate(train):xf[i,5*1024]=r['answer']
        for i,r in enumerate(dev):xd[i,5*1024]=r['answer']
        arms={'localized':[5]}
        folds=[{'fold':fi,'fit':np.arange(3),'val':np.arange(3,6),
             'fit_games':['fit'],'val_games':['val'],
             'test':np.arange(3*fi,3*(fi+1)), 'test_games':[f'unseen_{"a" if fi==0 else "b"}']}
             for fi in range(2)]
        parent_record={'source_causal':str(causal),'settings':{'folds':2,'seed':47},
          'arms':arms,'holdout':{'folds':[{'heldout_games':f['test_games'],'test_cases':3} for f in folds]}}
        mock_lineage=(parent,parent_record,{'checkpoint_sha256':'frozen'},
            {'contexts':[]},[5],exp,source,capture,originals)
        monkeypatch.setattr(cal.repair,'_lineage',lambda p:mock_lineage)
        monkeypatch.setattr(cal.suff,'_cached_split',lambda e,s,i,k:
             (train,xf,exp/'feature_cache'/'train.npz') if k=='train' else
             (dev,xd,exp/'feature_cache'/'dev.npz'))
        monkeypatch.setattr(cal,'_folds',lambda *a:folds)
        source_fingerprints=before.copy()
        prior_rows=[]
        for fi,f in enumerate(folds):
            net=suff._model(torch,1024,8)
            path=root/'experimental_models'/f'fold{fi+1:02d}_localized.pt'
            suff._save_state(torch,path,{k:v.detach().cpu() for k,v in net.state_dict().items()},
                {'experiment_schema':repair.SCHEMA,'fold':fi,'mode':'localized',
                 'positions':[5],'source_checkpoint_sha256':'frozen'})
            pred,cls,pr=suff._predict(torch,net,xd,f['test'],[5],device='cpu',batch_size=6)
            for i,p,c,q in zip(f['test'],pred,cls,pr):
                prior_rows.append({'arm':'localized','fold':fi,'index':int(i),'cohort':'unseen',
                    'class':int(c),'prediction':float(p)})
        with (root/'predictions.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=prior_rows[0].keys());w.writeheader();w.writerows(prior_rows)
        mm=repair._cell_metrics([{'target':dev[int(r['index'])]['answer'],
              'prediction':r['prediction'],'class':r['class']} for r in prior_rows])
        rr={'schema':repair.SCHEMA,'status':'COMPLETE_DISPOSABLE_NOT_PROMOTED',
            'source_generalization_or_sufficiency':str(parent),
            'source_expanded':str(exp),'source_causal':str(causal),
            'source_fingerprints':source_fingerprints,
            'settings':{'hidden':8,'folds':2,'original_split_seed':47},
            'coverage':{'certified_dev_cases':6},
            'arms':arms,'results':{'localized':{'unseen_all':{'unseen':{'all':mm}}}}}
        (root/'final_report.json').write_text(json.dumps(rr))
        result=cal.run_calibration(root,device='cpu',batch_size=16)
        assert result['status']=='COMPLETE_READ_ONLY_NOT_PROMOTED'
        assert result['coverage']['heldout_cases']==6
        assert len(result['thresholds']['localized'])==2
        assert result['results']['localized']['all']['raw']['cases']==6
        assert result['results']['localized']['all']['threshold_calibrated']['cases']==6
        assert (tmp_path/'balanced-calibration-0001'/'predictions.csv').is_file()
        assert all(suff._digest(p)==h for p,h in before.items())
        assert result['promotion'].startswith('BLOCKED_')
    finally:torch.set_num_threads(prev)
