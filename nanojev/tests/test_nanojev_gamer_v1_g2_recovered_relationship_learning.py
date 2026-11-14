"""G2-0039 protocol: real scorer training, frozen parent, TRAIN-only policy gate."""
from __future__ import annotations

import copy
import json

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_recovered_relationship_learning as g


def toy_example(expanded=False):
    torch.manual_seed(399 if expanded else 398)
    anchors=['a','b','c','d']
    edges=[('a','b','rel'),('a','c','rel'),('a','d','rel'),
           ('b','c','rel'),('b','d','rel'),('b','a','rel')]
    example={'windows':torch.randn(4,5,16),
        'window_mask':torch.ones(4,5,dtype=torch.bool),
        'src':torch.tensor([0,0,0,1,1,1],dtype=torch.long),
        'dst':torch.tensor([1,2,3,2,3,0],dtype=torch.long),
        'reference':torch.full((6,),float(g.m29.LOGIT_CUTOFF)),
        'relation':torch.zeros(6,dtype=torch.long),
        'group':torch.tensor([0,0,0,1,1,1],dtype=torch.long),
        'labels':torch.tensor([1.,0.,0.,1.,0.,0.]),
        'edges':edges,'original_anchors':('a','b','c','d')}
    if expanded:
        example['edges'][5]=('new','b','rel')
    return example


def test_training_finetunes_original_g2_without_mutating_parent(monkeypatch):
    torch.set_num_threads(1)
    parents={s:{'occurrence_graph':g.x31.OccurrenceGraph(16).eval().requires_grad_(False)}
             for s in g.SEEDS}
    first={s:{k:v.detach().clone() for k,v in parents[s]['occurrence_graph'].state_dict().items()}
           for s in g.SEEDS}
    cases=[('original',{'prompt':'x'},toy_example(False)) for _ in range(8)]+[
           ('recovered_s460331',{'prompt':'y'},toy_example(True)) for _ in range(8)]
    trained,audit=g.train_relationships(cases,parents,steps=2)
    assert set(trained)==set(g.SEEDS)
    for seed in g.SEEDS:
        assert audit[str(seed)]['original_hard_examples']==8
        assert audit[str(seed)]['expanded_hard_examples']==8
        assert all(torch.equal(v,first[seed][k]) for k,v in
                   parents[seed]['occurrence_graph'].state_dict().items())
        assert not any(p.requires_grad for p in trained[seed].parameters())
        assert any(not torch.equal(v,first[seed][k]) for k,v in
                   trained[seed].state_dict().items())


def test_hard_negative_loss_targets_only_false_edges_of_added_endpoints():
    ex=toy_example(True)
    scores=torch.full((6,),float(g.m29.LOGIT_CUTOFF),requires_grad=True)
    loss=g.hard_negative_loss(scores,ex,set(ex['original_anchors']))
    assert float(loss.detach())>0.
    loss.backward()
    assert scores.grad[5]!=0
    assert torch.count_nonzero(scores.grad[:5])==0
    assert g.hard_negative_loss(scores,toy_example(False),set(ex['original_anchors']))==0.


def test_blending_never_mutates_baseline_and_requires_declared_weight():
    original={'raw':{'occurrence_graph_s'+str(s):np.array([1.,2.],np.float32)
                     for s in g.SEEDS},'edges':[1,2],'groups':np.array([0,0])}
    newer={'raw':{'occurrence_graph_s'+str(s):np.array([3.,4.],np.float32)
                  for s in g.SEEDS}}
    assert g.interpolate(original,newer,0.)['raw']['occurrence_graph_s460331'].tolist()==[1.,2.]
    assert g.interpolate(original,newer,.5)['raw']['occurrence_graph_s460331'].tolist()==[2.,3.]
    assert original['raw']['occurrence_graph_s460331'].tolist()==[1.,2.]
    with pytest.raises(ValueError,match='UNCALIBRATED'):
        g.interpolate(original,newer,.6)


def test_policy_requires_train_fit_and_disjoint_audit(monkeypatch):
    def grid(records,*args):
        audit=records=='audit'
        result={}
        for seed in g.SEEDS:
            for a in g.BLENDS:
                acceptable=a==.5
                result[f'{seed}:{a:.2f}']={'f1_base':10.,
                    'f1_new':11. if acceptable else 9.,
                    'exact_base':3,'exact_new':3,
                    'ranking_base':20,'ranking_new':19 if acceptable else 22,
                    'fp_base':30,'fp_new':29 if acceptable else 31,'cases':40}
                if audit and seed==g.SEEDS[-1] and acceptable:
                    result[f'{seed}:{a:.2f}']['ranking_new']=21
        return result
    monkeypatch.setattr(g,'calibration_grid',grid)
    policies,audit=g.fit_policies('fit','audit',{},None,None,None,None)
    assert policies[g.SEEDS[0]]=={'enabled':True,'blend':.5}
    assert policies[g.SEEDS[1]]=={'enabled':True,'blend':.5}
    assert policies[g.SEEDS[2]]['enabled'] is False
    assert policies[g.SEEDS[2]]['disabled_reason']=='failed_independent_train_audit'
    assert len(audit[str(g.SEEDS[0])]['fit_grid'])==len(g.BLENDS)


def test_policy_fail_closed_with_zero_train_fit_benefit(monkeypatch):
    def grid(records,*args):
        return {f'{seed}:{a:.2f}':{'f1_base':10.,'f1_new':10.,
            'exact_base':1,'exact_new':1,'ranking_base':3,'ranking_new':3,
            'fp_base':1,'fp_new':1,'cases':2}
            for seed in g.SEEDS for a in g.BLENDS}
    monkeypatch.setattr(g,'calibration_grid',grid)
    result,_=g.fit_policies('fit','audit',{},None,None,None,None)
    assert all(not p['enabled'] and p['blend']==0 for p in result.values())


def test_parent_rejects_wrong_schema(tmp_path):
    (tmp_path/g.x38.REPORT).write_text(json.dumps({'schema':'forged'}))
    with pytest.raises(ValueError,match='PARENT_0038_CONTRACT:schema'):
        g.verify_parent38(tmp_path,ancestors={},train_sha='a',pool38=[],dev38={})


def test_dev_inference_never_receives_gold(monkeypatch):
    sample={'anchors':['a','b'],'gold':set()}
    flags=[]
    def example(case,row,state,**kwargs):
        flags.append(case['gold'])
        return {'edges':[('a','b','r')], 'group':torch.tensor([0])}
    monkeypatch.setattr(g.x31,'example_from_case',example)
    monkeypatch.setattr(g.x31,'score_case',lambda ex,state,models:{
        'raw':{f'occurrence_graph_s{s}':np.array([0.],dtype='float32')
               for s in g.SEEDS},'edges':ex['edges'],
        'groups':np.array([0]),'gold':set()})
    class Net(torch.nn.Module):
        def __init__(self):
            super().__init__();self.p=torch.nn.Parameter(torch.tensor(0.))
        def forward(self,data):return torch.tensor([1.])
    monkeypatch.setattr(g.x31,'scorer_input',lambda ex,dev:{})
    old,new=g.score_pair(sample,{'prompt':'x'}, {},{},
                         {s:Net() for s in g.SEEDS},device='cpu')
    assert flags==[set()]
    assert old['raw']['occurrence_graph_s460331'][0]==0
    assert new['raw']['occurrence_graph_s460331'][0]==1


def test_cli_registers_context38_parent():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-recovered-relationships','--help'],capture_output=True,text=True,check=True)
    assert '--context38-run' in result.stdout
    assert '--device' in result.stdout


def test_development_gate_requires_exact_gain_not_only_f1():
    def summary(exact, f1, ranked):
        return {'exact_cases':exact,'f1':f1,'ranking_failures':ranked}
    full={'models':{}}
    top={'models':{}}
    for s in g.SEEDS:
        for mode in ('group','structured'):
            key=mode+'_s'+str(s)
            full['models']['contextual__'+key]=summary(3,.70,7)
            full['models']['retrained__'+key]=summary(3,.73,6)
            top['models']['contextual__'+key]=summary(0,.5,6)
            top['models']['retrained__'+key]=summary(0,.55,6)
    policies={s:{'enabled':True,'blend':.5} for s in g.SEEDS}
    fail=g.development_gates(full,top,{},policies)
    assert not fail['exact_graph_improved_without_regressions']
    for s in g.SEEDS:
        for mode in ('group','structured'):
            full['models'][f'retrained__{mode}_s{s}']['exact_cases']=4
    ok=g.development_gates(full,top,{},policies)
    assert ok['exact_graph_improved_without_regressions']


def test_exact_transition_audit_rejects_trading_away_correct_graphs():
    rows=[]
    for old,new in ((1,0),(0,1),(0,1),(1,1)):
        metrics={}
        for seed in g.SEEDS:
            for mode in ('group','structured'):
                metrics[f'contextual__{mode}_s{seed}']={'exact_cases':old}
                metrics[f'retrained__{mode}_s{seed}']={'exact_cases':new}
        rows.append({'metrics':metrics})
    switched=g.exact_transitions(rows)
    assert switched['group_s460331']=={'newly_exact':2,'lost_exact':1,
                                        'retained_exact':1}
    all_models={}
    topo_models={}
    for seed in g.SEEDS:
        for mode in ('group','structured'):
            key=f'{mode}_s{seed}'
            all_models[f'contextual__{key}']={'exact_cases':2,'f1':.7,
                                              'ranking_failures':7}
            all_models[f'retrained__{key}']={'exact_cases':3,'f1':.75,
                                             'ranking_failures':6}
            topo_models[f'contextual__{key}']={'exact_cases':0,'f1':.5}
            topo_models[f'retrained__{key}']={'exact_cases':0,'f1':.51}
    gates=g.development_gates({'models':all_models},{'models':topo_models},{},
        {seed:{'enabled':True,'blend':.5} for seed in g.SEEDS},switched)
    assert not gates['exact_graph_improved_without_regressions']
    assert gates['by_seed']['460331']['by_decoder']['group']['lost_exact']==1
