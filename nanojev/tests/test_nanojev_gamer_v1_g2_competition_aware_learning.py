"""CPU/contract tests for the development-only G2-0022 competition experiment."""
from __future__ import annotations

from copy import deepcopy
import gzip
import json
import math
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_competition_aware_learning as m
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as p21
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_locked_design_and_control_flag():
    assert m.STEPS==320 and m.LR==6.e-5 and m.TOP_K==24
    assert m.THRESHOLD==.75
    assert m.ARMS==('frozen_guard_0019','matched_finetune','competition_aware')
    assert m.REPLAY==p21.ASSESS
    assert m.NEW==('new_independent_dev_0022','new_topology_dev_0022')


def test_hard_negative_ranking_is_multi_positive_and_local():
    logits=torch.tensor([.3,2.,1.,.1,2.5,1.],requires_grad=True)
    labels=torch.tensor([1.,1.,0.,0.,1.,0.])
    groups=[3,3,3,3,4,4]
    loss,counts=m.local_hard_negative_loss(logits,labels,groups,top_hard=1)
    assert counts=={'ranked_groups':2,'hard_pairs':3}
    loss.backward()
    assert logits.grad[0]<0 and logits.grad[1]<0 and logits.grad[4]<0
    assert logits.grad[2]>0 and logits.grad[5]>0
    assert logits.grad[3]==0 # Lower-scoring negative excluded by hard mining
    # No positives are excluded just because another destination is stronger.
    changed=logits.detach().clone();changed[0]+=2
    better,_=m.local_hard_negative_loss(changed,labels,groups,top_hard=1)
    assert better<loss


def test_hard_negative_no_eligible_pairs_has_zero_finite_gradient():
    x=torch.tensor([2.,3.,4.],requires_grad=True)
    out,counts=m.local_hard_negative_loss(x,torch.ones(3),[0,0,1])
    assert out.item()==0 and counts['hard_pairs']==0
    out.backward()
    assert torch.equal(x.grad,torch.zeros_like(x))
    with pytest.raises(ValueError,match='BAD_RANKING_INPUT'):
        m.local_hard_negative_loss(torch.tensor([float('nan')]),torch.ones(1),[0])
    with pytest.raises(ValueError,match='BAD_RANKING_INPUT'):
        m.local_hard_negative_loss(torch.tensor([0.]),torch.tensor([2.]),[0])


def test_matched_arm_matches_0019_objective_experimental_has_extra_pressure():
    logits=torch.tensor([.0,2.,1.,.5],requires_grad=True)
    target=torch.tensor([1.,1.,0.,0.]);null=torch.zeros(4);far=torch.zeros(4)
    group=[0,0,0,0]
    control=m.joint_objective(logits,target,null,far,group,arm='matched_finetune')
    base_obj=m.train19.training_objective(logits,target,null,far,group,arm='precision_guard')
    experimental=m.joint_objective(logits,target,null,far,group,arm='competition_aware')
    assert control==base_obj
    assert experimental>control
    with pytest.raises(ValueError,match='UNKNOWN_ARM'):
        m.joint_objective(logits,target,null,far,group,arm='frozen_guard_0019')


class SimpleScorer(torch.nn.Module):
    def __init__(self):
        super().__init__();self.w=torch.nn.Parameter(torch.tensor(1.))
    def forward(self,x,b,groups,*,with_competition):
        assert with_competition
        return self.w*x[:,0]+b*.01


def _training_case(family):
    edges=[((0,1,'actor'),(i+2,i+3,'action'),'owns') for i in range(4)]
    return {'family':family,'split':'train','prompt_sha256':family,
            'gold':{edges[0],edges[2]},'rank':None,'allowed':set(),
            'anchors':[],'vectors':torch.zeros(1,4),'edges_for_test':edges}


def _make_inventory(case,rank,allowed,*,top_k,train_gold):
    assert top_k==24 and train_gold is False
    edges=case['edges_for_test']
    # No gold-conditioned injection or selection; every group is present.
    return {'edges':edges,'groups':[0,0,0,0],
            'features':torch.tensor([[.2],[2.],[1.],[.1]]),
            'base':torch.zeros(4),'null_argmax':[False]*4}


def test_train_is_fair_frozen_and_train_only(monkeypatch):
    monkeypatch.setattr(m,'STEPS',4)
    cases=[_training_case('encounter'),_training_case('allocation')]
    frozen=SimpleScorer();state={k:v.clone() for k,v in frozen.state_dict().items()}
    trained,info=m.train(cases,frozen,steps=4,lr=m.LR,device='cpu',
                         inventory_fn=_make_inventory)
    assert set(trained)==set(m.ARMS[1:])
    assert info['common_initialization'] and info['common_batches']
    assert info['train_cases']==2 and info['counts']['positives']>0
    assert all(torch.equal(frozen.state_dict()[k],v) for k,v in state.items())
    assert all(not p.requires_grad for p in frozen.parameters())
    assert info['last_train_loss']['matched_finetune'] != info['last_train_loss']['competition_aware']
    modified=deepcopy(cases);modified[0]['split']='dev'
    with pytest.raises(ValueError,match='NONTRAIN_LABEL'):
        m.train(modified,SimpleScorer(),steps=4,lr=m.LR,inventory_fn=_make_inventory)
    modified=deepcopy(cases);modified[0]['structural_topology']='4x4'
    with pytest.raises(ValueError,match='NONTRAIN_LABEL'):
        m.train(modified,SimpleScorer(),steps=4,lr=m.LR,inventory_fn=_make_inventory)


def test_competition_counts_correct_edges_not_outranking_negative():
    edges=['goldA','goldB','wrong','other']
    result=m._competition_failures(edges,[0,0,0,1],torch.tensor([1.,2.,1.5,40.]),
                                   {'goldA','goldB'})
    assert result=={'gold_with_competing_negatives':2,
                    'gold_not_ahead_of_hardest_negative':1}


def test_fresh_0022_cohorts_strictly_disjoint(monkeypatch):
    history=[{'prompt':'prior'}]
    p20={p21.SELECT[0]:[{'prompt':'p20i'}],p21.SELECT[1]:[{'prompt':'p20t'}]}
    p21rows={p21.ASSESS[0]:[{'prompt':'p21i'}],p21.ASSESS[1]:[{'prompt':'p21t'}]}
    def independent(prior,*,limit,seed):
        assert seed==460025 and limit==320
        assert {'prior','p20i','p20t','p21i','p21t'}<=set(r['prompt'] for batch in prior for r in batch)
        return [{'prompt':f'i-{i}','split':'dev'} for i in range(limit)]
    def topo(prior,*,limit,seed):
        assert seed==460026 and limit==128
        return [{'prompt':f't-{i}','split':'dev',
                 'structural_topology':('4x4','4x2','2x4')[i%3]} for i in range(limit)]
    monkeypatch.setattr(m.general16,'independent_rows',independent)
    monkeypatch.setattr(m.general16,'heldout_topology_rows',topo)
    out=m.fresh_cohorts(history,p20,p21rows)
    assert {len(out[s]) for s in out}=={128,320}
    monkeypatch.setattr(m.general16,'independent_rows',lambda *args,**kw:
                        [{'prompt':'p21i','split':'dev'}]*320)
    with pytest.raises(ValueError,match='COHORT_CONTRACT'):
        m.fresh_cohorts(history,p20,p21rows)


def test_case_evaluation_replays_entire_parent_score_set(monkeypatch):
    anchors=[(0,1,'actor'),(2,3,'action'),(4,5,'action')]
    edges=[(anchors[0],anchors[1],'owns'),(anchors[0],anchors[2],'owns')]
    case={'prompt_sha256':base._sha('prompt'), 'family':'encounter',
          'anchors':anchors,'gold':{edges[0]},'vectors':torch.randn(3,4)}
    row={'prompt':'prompt','family':'encounter','split':'dev'}
    def inv(*args,**kwargs):
        assert kwargs['train_gold'] is False and kwargs['top_k']==24
        return {'edges':edges,'groups':[0,0],
                'features':torch.tensor([[2.],[0.]]),'base':torch.zeros(2)}
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:torch.zeros(3,3,len(BIND_CLASSES)))
    monkeypatch.setattr(m.source13,'make_inventory',inv)
    guard=SimpleScorer()
    out=m.evaluate_case(case,row,None,guard,{'matched_finetune':SimpleScorer(),
        'competition_aware':SimpleScorer()},set(),split=m.REPLAY[0],index=1,device='cpu')
    assert out['reachable_gold']==1
    assert out['configurations']['frozen_guard_0019']['correct']==1
    old={'prompt_sha256':case['prompt_sha256'],'fixed_score':out['configurations']['frozen_guard_0019'],
         'reachable_gold':1,'candidates':[{'edge':[[0,1,'actor'],[2,3,'action'],'owns'],
                                         'score':2.},
                                        {'edge':[[0,1,'actor'],[4,5,'action'],'owns'],
                                         'score':0.}]}
    assert m.evaluate_case(case,row,None,guard,{'matched_finetune':SimpleScorer(),
        'competition_aware':SimpleScorer()},set(),split=m.REPLAY[0],index=1,
                           device='cpu',parent21=old)==out
    old['candidates'][0]['score']+=.1
    with pytest.raises(ValueError,match='REPLAY_DRIFT'):
        m.evaluate_case(case,row,None,guard,{'matched_finetune':SimpleScorer(),
            'competition_aware':SimpleScorer()},set(),split=m.REPLAY[0],index=1,
                            device='cpu',parent21=old)


def test_parent_21_rejects_invalid_manifest_and_output_is_immutable(tmp_path):
    parent=tmp_path/'parent';parent.mkdir()
    (parent/p21.REPORT).write_text('{"schema":"bad"}')
    with pytest.raises(ValueError,match='PARENT_0021_CONTRACT'):
        m._read_parent21(parent,{}, {})
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,output=tmp_path,g1_validation_report=None,g2_run=None,
            selection_run=None,discrimination_run=None,relational_run=None,
            density_run=None,consistency_run=None,context_run=None,learned_run=None,
            recall_run=None,crowded_run=None,robustness_run=None,diagnosis_run=None,
            source_run=None,specialization_run=None,routing_robustness_run=None,
            general_run=None,ceiling_run=None,context18_run=None,train19_run=None,
            ceiling20_run=None,ceiling21_run=None)


def test_reproducible_gzip_and_diagnostic_gates(tmp_path):
    def case(index,split):
        return {'index':index,'group':split,'family':'encounter',
                'density_bucket':'medium_25_48','structural_topology':'native',
                'gold_total':2,'reachable_gold':2,
                'configurations':{a:{'correct':1,'missing':1,'false_positive':1,'exact':False}
                                  for a in m.ARMS},
                'competition':{a:{'gold_with_competing_negatives':2,
                                  'gold_not_ahead_of_hardest_negative':1} for a in m.ARMS}}
    records={s:[case(i,s)] for i,s in enumerate(m.SPLITS,1)}
    p1=tmp_path/'a.gz';p2=tmp_path/'b.gz'
    m._save_cases(records,p1);m._save_cases(records,p2)
    assert p1.read_bytes()==p2.read_bytes()
    with gzip.open(p1,'rt') as f: assert len(f.readlines())==len(m.SPLITS)
    a=m.aggregate([case(1,m.NEW[0])])
    assert a['reachable_gold']==2
    assert not m.gates(a)['competition_f1_above_matched_control']
    assert m.gates(a)['competition_recall_at_least_matched_minus_002']
