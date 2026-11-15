"""G2-0045: frozen-feature linear/nonlinear learnability and objective isolation."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from torch.nn import functional as F

from tools import nanojev_gamer_v1_g2_separability_diagnosis as d


def example(monkeypatch,prompt='synthetic-0045'):
    parent=d.x33.GraphSetEnergy().eval().requires_grad_(False)
    edges=[('a','b','rel'),('a','c','rel'),('d','e','rel'),('d','f','rel')]
    gold={edges[0],edges[2]}
    features={'edges':np.zeros((4,d.x33.EDGE_DIM),dtype=np.float32),
              'groups':np.zeros((2,d.x33.GROUP_DIM),dtype=np.float32),
              'index':np.array([0,0,1,1],dtype=np.int64)}
    features['edges'][:,0]=np.array([.8,-.7,.6,-.5])
    features['edges'][:,1]=np.array([-.3,-.5,-.2,-.4])
    features['groups'][:,2]=[.3,-.3]
    with torch.inference_mode():
        x,g,i=d.x33._tensor_features(features,'cpu')
        logits,cnt,total=parent(x,g,i)
    structured_pred=(logits.detach().double().numpy(),
                     F.log_softmax(cnt,-1).detach().double().numpy(),
                     float(torch.expm1(total).item()))
    rec={'row':{'split':'train','prompt':prompt,'family':'synthetic','gold':gold},
         'shortlisted':{'edges':edges},'features':features,
         'structured_pred':structured_pred,
         'opportunity':{'repairable':True,'shortlist_representable':True},
         'admitted':True}
    monkeypatch.setattr(d.base,'gold_binding_edges',lambda row:row['gold'])
    policy={'enabled':True,'source':d.g40.SOURCE,'group_weight':.7,
            'global_weight':0.,'fallback':None}
    return rec,parent,policy


def test_0044_parent_artifacts_verified():
    folder=Path('/mnt/data')
    if not (folder/d.d44.REPORT).exists():
        pytest.skip('uploaded parent G2-0044 not mounted')
    report=json.loads((folder/d.d44.REPORT).read_text(encoding='utf-8'))
    hashes=report['parent_hashes']
    args=[{k:v for k,v in hashes.items() if k.startswith(f'g2_{n}_')}
          for n in ('0040','0041','0042','0043')]
    verified,out=d.verify_parent44(folder,*args)
    assert verified['direct_logit_fit']['first_exact_step']==5
    assert out['g2_0044_ledger_sha256']==verified['ledger_sha256']


def test_frozen_features_and_identical_initial_score(monkeypatch):
    record,parent,policy=example(monkeypatch)
    frozen=d.extract_frozen(record,parent)
    assert frozen[0].shape==(4,d.x33.HIDDEN)
    initial=torch.as_tensor(record['structured_pred'][0],dtype=torch.float32)
    for kind in d.KINDS:
        net=d.Readout(kind,parent)
        with torch.inference_mode():
            scores=net(frozen[0],frozen[1])
        assert torch.allclose(scores,initial,atol=2e-4)
        assert all(torch.equal(v,parent.state_dict()[k]) for k,v in parent.state_dict().items())
        if kind=='nonlinear':
            assert all(not x.requires_grad for x in net.base.parameters())


def test_direct_score_reaches_real_decoder_and_no_gold_leaks_to_features(monkeypatch):
    record,parent,policy=example(monkeypatch)
    assert d.d44.direct_gold_oracle(record,policy)
    assert d.direct_control(record,policy,steps=250)['exact']
    sig1=d.extract_frozen(record,parent)
    other=copy.deepcopy(record)
    other['row']['gold']={record['shortlisted']['edges'][1]}
    sig2=d.extract_frozen(other,parent)
    assert torch.equal(sig1[0],sig2[0])
    assert torch.equal(sig1[1],sig2[1])


def test_loss_components_distinct_and_gradients_finite(monkeypatch):
    record,parent,policy=example(monkeypatch)
    h,prior,_=d.extract_frozen(record,parent)
    truth=torch.as_tensor([e in record['row']['gold'] for e in record['shortlisted']['edges']],
                          dtype=h.dtype)
    for kind in d.KINDS:
        outputs=[]
        for variant in d.LOSSES:
            model=d.Readout(kind,parent)
            out=model(h,prior)
            loss,parts=d.objective(out,truth,record,policy,variant,
                                   count_pred=record['structured_pred'])
            assert torch.isfinite(loss)
            assert set(parts)=={'bce','margin','structured','count_penalty'}
            loss.backward()
            assert all(p.grad is not None and torch.isfinite(p.grad).all()
                       for p in model.parameters() if p.requires_grad)
            outputs.append(float(loss.detach()))
        assert len(set(round(x,6) for x in outputs))>=3


def test_training_one_step_scope_and_dev_refusal(monkeypatch):
    record,parent,policy=example(monkeypatch)
    prior={k:v.clone() for k,v in parent.state_dict().items()}
    for kind in d.KINDS:
        net,stats=d.fit_case(record,parent,policy,kind,'bce',steps=2,checkpoint_every=1)
        assert stats['steps_completed']==2
        assert len(stats['history'])>=3
        assert all(torch.equal(parent.state_dict()[k],v) for k,v in prior.items())
        assert net(d.extract_frozen(record,parent)[0],d.extract_frozen(record,parent)[1]).shape==(4,)
    dev=copy.deepcopy(record);dev['row']['split']='dev'
    with pytest.raises(ValueError,match='DEV_SUPERVISION_FORBIDDEN'):
        d.objective(torch.zeros(4,requires_grad=True),torch.zeros(4),dev,policy,'bce',
                    count_pred=record['structured_pred'])
    with pytest.raises(ValueError,match='TRAIN_CONTRACT'):
        d.fit_case(dev,parent,policy,'linear','bce',steps=1)


def test_train_case_selection_simple_and_hard_disjoint(monkeypatch):
    rec,parent,policy=example(monkeypatch)
    records=[]
    for i in range(20):
        r=copy.deepcopy(rec)
        r['row']['prompt']=f'different-{i}'
        r['row']['gold']=set(rec['shortlisted']['edges'][:1+(i%3)])
        records.append(r)
    hard=d.d44._sha(records[0])
    parent44={'mini_train_prompts':[hard]}
    chosen,unseen=d.select_cases(records,parent44)
    assert d.d44._sha(chosen['hard'])==hard
    assert len(chosen['simple']['row']['gold'])==1
    shas={d.d44._sha(r) for r in [*chosen.values(),*unseen]}
    assert len(shas)==8
    assert len(unseen)==d.N_HELDOUT
    with pytest.raises(ValueError,match='TRAIN_REACHABILITY'):
        d.select_cases([rec],parent44)


def test_separation_conclusion_bounded():
    base={'direct_controls':{'simple':{'exact':True},'hard':{'exact':True}},
          'experiments':{'simple':{
              'linear:bce':{'ever_exact':False},
              'linear:margin':{'ever_exact':False},
              'nonlinear:bce':{'ever_exact':False}}}}
    assert d.classify(base)=='NEITHER_SHARED_HEAD_MEMORIZED_WITHIN_TESTED_BUDGETS'
    base['experiments']['simple']['nonlinear:bce']['ever_exact']=True
    assert d.classify(base)=='NONLINEAR_FITS_WHERE_TESTED_LINEAR_DOES_NOT'
    base['experiments']['simple']['linear:margin']['ever_exact']=True
    assert d.classify(base)=='LINEAR_FITS_ONLY_WITH_DIFFERENT_OBJECTIVE'
    base['experiments']['simple']['linear:bce']['ever_exact']=True
    assert d.classify(base)=='LINEAR_FROZEN_FEATURES_SUFFICIENT_WITH_TESTED_OBJECTIVE'


def test_cli_registered():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
            'frob-g2-separability-diagnosis','--help'],check=True,text=True,
            capture_output=True)
    assert '--memorization44-run' in proc.stdout
    assert '--simple-steps' in proc.stdout
    assert '--hard-steps' in proc.stdout
    assert len(d.KINDS)*len(d.LOSSES)==12


def test_complete_diagnostic_writes_immutable_train_only_outputs(monkeypatch,tmp_path):
    rec,parent,policy=example(monkeypatch)
    train=[];fit=[];audit=[]
    for section,destination,count in [('train',train,20),('fit',fit,3),('audit',audit,3)]:
        for i in range(count):
            item=copy.deepcopy(rec)
            item['row']['prompt']=f'{section}-{i:02d}'
            destination.append(item)
    parent44={'mini_train_prompts':[d.d44._sha(train[0])],
              'training_sha256':{},'train_dataset_sha256':'synthetic'}
    monkeypatch.setattr(d,'fit_case',lambda item,parent,policy,kind,variant,**kwargs:(
        d.Readout(kind,parent).eval().requires_grad_(False),{
            'steps_completed':1,'first_exact_checkpoint':None,
            'memorized':False,'ever_exact':False,'history':[],
            'best_training_checkpoint':{},'train_final':{}}))
    out=tmp_path/'results'
    result=d.stage2(train,fit,audit,policy,parent,lambda:None,out,parent44,{})
    assert result['development_evaluation_skipped']
    assert result['production_promotion'] is False
    assert result['train_policy']['enabled'] is False
    assert not result['dev_labels_used_for_training']
    assert not result['sealed_test_used']
    assert len(result['experiments'])==2
    assert all(len(run)==12 for run in result['experiments'].values())
    for name in (d.REPORT,d.LEDGER,d.WEIGHTS,d.POLICIES):
        assert (out/name).is_file()
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.stage2(train,fit,audit,policy,parent,lambda:None,out,parent44,{})
