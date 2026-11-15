"""G2-0043: three non-oracle score-learning arms, parent integrity, gates."""
import copy
import json
import subprocess
import sys

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_absolute_score_bakeoff as b


def fixture(monkeypatch):
    edges=[('a','b','rel'),('a','c','rel'),('d','e','rel'),('d','f','rel')]
    truth={edges[0],edges[2]}
    old=np.array([-3.,2.,-3.,2.])
    counts=np.full((2,b.x33.COUNT_CLASSES),-50.)
    counts[:,1]=0.
    features={'edges':np.zeros((4,b.x33.EDGE_DIM),dtype=np.float32),
              'groups':np.zeros((2,b.x33.GROUP_DIM),dtype=np.float32),
              'index':np.array([0,0,1,1],dtype=np.int64)}
    policy={'enabled':True,'source':b.g40.SOURCE,'group_weight':.7,
            'global_weight':0.,'fallback':None}
    record={'row':{'prompt':'test','gold':truth,'split':'train'},
            'admitted':True,'features':features,
            'shortlisted':{'edges':edges},
            'structured_pred':(old,counts,2.),
            'opportunity':{'shortlist_representable':True,'repairable':True}}
    monkeypatch.setattr(b.base,'gold_binding_edges',lambda row:row['gold'])
    return record,truth,policy


def test_three_independent_learning_arms(monkeypatch):
    record,gold,policy=fixture(monkeypatch)
    parent=b.x33.GraphSetEnergy().eval().requires_grad_(False)
    state={k:v.clone() for k,v in parent.state_dict().items()}
    for kind in b.KINDS:
        model=b.ScoreRepair(parent,kind)
        preds=model.predict(record['features'])
        assert len(preds[0])==4
        assert len(preds[1])==2
        assert all(np.isfinite(x).all() for x in preds[:2])
        loss,parts=b.train_objective(model,record,policy)
        assert np.isfinite(parts['loss'])
        loss.backward()
        assert model.core.edge_readout.weight.grad is not None
        if kind=='group_margin':
            assert model.group_adapter[-1].weight.grad is not None
            assert not model.core.group_readout[0].weight.requires_grad
        elif kind=='joint_count':
            assert model.core.group_readout[0].weight.grad is not None
            assert model.core.graph_readout[0].weight.grad is not None
        else:
            assert not model.core.group_readout[0].weight.requires_grad
            assert not model.core.graph_readout[0].weight.requires_grad
        assert all(torch.equal(parent.state_dict()[k],v) for k,v in state.items())


def test_train_protects_parent_weights_and_head_scope(monkeypatch):
    record,gold,policy=fixture(monkeypatch)
    records=[]
    for i in range(20):
        r=copy.deepcopy(record)
        r['row']['prompt']=f'case-{i}'
        r['admitted']=i<10
        r['opportunity']={'shortlist_representable':True,'repairable':i<10}
        r['structured_policy']=policy
        records.append(r)
    parent=b.x33.GraphSetEnergy().eval().requires_grad_(False)
    before={k:v.clone() for k,v in parent.state_dict().items()}
    for kind in b.KINDS:
        model,last=b.train_arm(records,parent,kind,steps=2)
        assert last['step']==2
        assert all(torch.equal(parent.state_dict()[k],v) for k,v in before.items())
        for key,v in before.items():
            if not key.startswith('edge_readout.') and not (kind=='joint_count' and
                  (key.startswith('group_readout.') or key.startswith('graph_readout.'))):
                assert torch.equal(model.core.state_dict()[key],v),key
        assert np.isfinite(np.asarray(model.predict(record['features'])[0])).all()


def test_unchanged_case_skips_candidate_prediction(monkeypatch):
    record,gold,policy=fixture(monkeypatch)
    record['admitted']=False
    expected={('a','b','rel')}
    monkeypatch.setattr(b.g40,'baseline_selection',lambda rec,arm:expected)
    result=b.choose(record,{'kind':'absolute','bias':1.},{},policy)
    assert result==expected


def test_train_labels_not_allowed_on_dev(monkeypatch):
    record,gold,policy=fixture(monkeypatch)
    record['row']['split']='dev'
    model=b.ScoreRepair(b.x33.GraphSetEnergy().eval().requires_grad_(False),'absolute')
    with pytest.raises(ValueError,match='DEV_TRAIN_FORBIDDEN'):
        b.train_objective(model,record,policy)
    with pytest.raises(ValueError,match='DEV_TRAIN_METRICS_FORBIDDEN'):
        b.measure([record],{'kind':'absolute','bias':0.},{'absolute':model},policy)


def test_no_shortlist_opportunity_does_not_produce_supervision(monkeypatch):
    record,gold,policy=fixture(monkeypatch)
    record['opportunity']['shortlist_representable']=False
    model=b.ScoreRepair(b.x33.GraphSetEnergy().eval().requires_grad_(False),'absolute')
    with pytest.raises(ValueError,match='UNREACHABLE_TRAIN_GRAPH'):
        b.train_objective(model,record,policy)


def test_real_parent42_upload_validates():
    from pathlib import Path
    folder=Path('/mnt/data')
    if not (folder/b.g42.REPORT).exists():pytest.skip('uploaded G2-0042 artifacts absent')
    parent=json.loads((folder/b.g42.REPORT).read_text())
    h40={k:v for k,v in parent['parent_hashes'].items() if k.startswith('g2_0040')}
    h41={k:v for k,v in parent['parent_hashes'].items() if k.startswith('g2_0041')}
    _,digests=b.verify_parent42(folder,h40,h41)
    assert 'g2_0042_weights_sha256' in digests


def test_selection_audit_is_veto_only(monkeypatch):
    monkeypatch.setattr(b,'candidate_configs',lambda:[{'kind':'absolute','bias':0.}])
    def metrics(records,*args,**kwargs):
        if records==['fit']:
            return {'new_exact':2,'lost_exact':0,'f1':.76,'parent_f1':.75,
                    'fp':8,'parent_fp':10,'rank_inversions':9,
                    'parent_rank_inversions':10},[]
        return {'new_exact':0,'lost_exact':1,'f1':.76,'parent_f1':.75,
                'fp':8,'parent_fp':10,'rank_inversions':9,
                'parent_rank_inversions':10},[]
    monkeypatch.setattr(b,'measure',metrics)
    p,rows=b.select(['fit'],['audit'],{}, {})
    assert not p['enabled'] and rows[0]['eligible_fit']
    assert p['disabled_reason']=='winner_vetoed_by_natural_train_audit'


def test_no_winner_skips_dev(monkeypatch,tmp_path):
    record,gold,policy=fixture(monkeypatch)
    parent=b.x33.GraphSetEnergy().eval().requires_grad_(False)
    monkeypatch.setattr(b,'train_arm',lambda records,parent,kind:(
        b.ScoreRepair(parent,kind),{'step':1,'loss':.1}))
    monkeypatch.setattr(b,'select',lambda *args:({'enabled':False,'config':None,
        'disabled_reason':'no_safe_train_fit_winner'},[]))
    def forbidden():raise AssertionError('DEV opened without a TRAIN winner')
    out=tmp_path/'results'
    info={'training_sha256':{},'train_dataset_sha256':'abc','opportunity_frontier':{}}
    result=b.stage2([record]*16,[record]*4,[record]*4,policy,parent,
                    forbidden,lambda:None,out,info,{})
    assert result['development_evaluation_skipped']
    assert (out/b.REPORT).exists() and (out/b.WEIGHTS).exists()
    assert result['train_policy']['enabled'] is False
    with pytest.raises(FileExistsError):b.persist(out,result,[],{},result['train_policy'])


def test_cli_registered():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-absolute-score-bakeoff','--help'],capture_output=True,
        text=True,check=True)
    assert '--rank42-run' in result.stdout and '--oracle41-run' in result.stdout


def test_candidate_grid_predeclared():
    configs=b.candidate_configs()
    assert len(configs)==len(b.KINDS)*len(b.BIASES)==9
    assert {c['kind'] for c in configs}==set(b.KINDS)
