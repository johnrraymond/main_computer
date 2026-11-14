"""G2-0023 capacity-matched contextual ablation: CPU, lineage, safety contracts."""
from __future__ import annotations

from copy import deepcopy
import gzip
import json
import math

import pytest
import torch

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as m
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


def test_locked_protocol():
    assert m.STEPS==320 and m.LR==6.e-5 and m.THRESHOLD==.75
    assert m.TOP_K==24 and m.GROUPS_PER_CASE==8
    assert m.REPLAY==m.parent22.NEW
    assert m.NEW==('new_independent_dev_0023','new_topology_dev_0023')
    assert m.TRAINED==('capacity_only_0023','relational_context_0023')
    assert 'matched_finetune_0022' in m.ARMS


def _features(n=5,width=4):
    torch.manual_seed(94)
    return torch.randn(n,4*width+source13.EXTRAS),torch.linspace(-2,2,n)


def test_context_initialization_exactly_matches_frozen_parent():
    guard=source13.SourceConditionedScorer(4)
    before={k:v.detach().clone() for k,v in guard.state_dict().items()}
    net=m.RelationalContextScorer.from_guard(guard)
    x,b=_features();groups=[0,0,0,1,1]
    with torch.inference_mode():
        original=guard(x,b,groups)
        assert torch.equal(net(x,b,groups,contextual=True),original)
        assert torch.equal(net(x,b,groups,contextual=False),original)
    for k,v in before.items(): assert torch.equal(net.state_dict()[k],v)
    assert all(torch.equal(guard.state_dict()[k],v) for k,v in before.items())
    assert set(net.state_dict())-set(guard.state_dict())=={
        k for k in net.state_dict() if k.startswith('rel_')}


def test_peer_attention_is_group_local_permutation_equivariant_and_not_exclusive():
    guard=source13.SourceConditionedScorer(4)
    net=m.RelationalContextScorer.from_guard(guard).eval()
    with torch.no_grad():
        net.rel_readout.weight.fill_(.12)
        net.rel_readout.bias.fill_(.04)
    x,b=_features();groups=[0,0,0,1,1]
    y=net(x,b,groups,contextual=True)
    permutation=[2,0,1,4,3]
    other=net(x[permutation],b[permutation],[groups[i] for i in permutation],contextual=True)
    assert torch.allclose(other,y[permutation],atol=1e-5,rtol=1e-5)
    assert torch.isfinite(y).all() and y.shape==(5,)
    y_local=net(x,b,groups,contextual=False)-guard(x,b,groups)
    changed=x.clone();changed[1]+=3.
    y_modified=net(changed,b,groups,contextual=True)-guard(changed,b,groups)
    y_base=net(x,b,groups,contextual=True)-guard(x,b,groups)
    assert not torch.allclose(y_modified[0],y_base[0],atol=1e-7)
    changed_local=net(changed,b,groups,contextual=False)-guard(changed,b,groups)
    assert torch.allclose(changed_local[0],y_local[0],atol=1e-6)
    other_group=x.clone();other_group[4]+=3
    outside=net(other_group,b,groups,contextual=True)-guard(other_group,b,groups)
    assert torch.allclose(outside[:3],y_base[:3],atol=1e-6)


def test_singleton_no_invented_context_and_group_limits():
    guard=source13.SourceConditionedScorer(4)
    net=m.RelationalContextScorer.from_guard(guard)
    with torch.no_grad():net.rel_readout.bias.fill_(2.)
    x,b=_features(1)
    assert torch.equal(net(x,b,[0],contextual=True),guard(x,b,[0]))
    assert torch.isfinite(net(x,b,[0],contextual=False)).all()
    assert torch.equal(net(x[:0],b[:0],[]),b[:0])
    with pytest.raises(ValueError,match='GROUP_EXCEEDS_TOP24'):
        x,b=_features(25);net(x,b,[0]*25)
    with pytest.raises(ValueError,match='COMPETITION_REQUIRED'):
        x,b=_features(2);net(x,b,[0,0],with_competition=False)
    with pytest.raises(TypeError,match='UNSUPPORTED_PARENT'):
        m.RelationalContextScorer.from_guard(torch.nn.Linear(2,2))


def _fake_case(family):
    edges=[((0,1,'actor'),(2+i*2,3+i*2,'action'),'owns') for i in range(4)]
    return {'family':family,'split':'train','prompt_sha256':family,
            'gold':{edges[0],edges[2]},'rank':None,'allowed':set(),
            'anchors':[],'vectors':torch.zeros(1,4),'edges_for_test':edges}


def _inventory(case,rank,allowed,*,top_k,train_gold):
    assert top_k==24 and train_gold is False
    edges=case['edges_for_test']
    return {'edges':edges,'groups':[0]*4,
            'features':torch.linspace(-1,1,4*(16+source13.EXTRAS)).reshape(4,-1),
            'base':torch.zeros(4),'null_argmax':[False]*4}


def test_training_uses_identical_train_only_batches_and_frozen_parent(monkeypatch):
    monkeypatch.setattr(m,'STEPS',3)
    guard=source13.SourceConditionedScorer(4)
    before={k:v.clone() for k,v in guard.state_dict().items()}
    trained,info=m._train([_fake_case('allocation'),_fake_case('encounter')],guard,
                          steps=3,device='cpu',inventory_fn=_inventory)
    assert set(trained)==set(m.TRAINED)
    assert info['same_training_batches'] and info['same_precision_guard_loss']
    assert info['counts']['positives']>0
    assert info['train_cases']==2
    assert sum(p.numel() for p in trained[m.TRAINED[0]].parameters())==sum(
        p.numel() for p in trained[m.TRAINED[1]].parameters())
    assert all(torch.equal(guard.state_dict()[k],v) for k,v in before.items())
    assert all(not p.requires_grad for p in guard.parameters())
    invalid=_fake_case('encounter');invalid['split']='dev'
    with pytest.raises(ValueError,match='FIXED_TRAIN_CONTRACT'):
        m._train([invalid,_fake_case('allocation')],source13.SourceConditionedScorer(4),
                 steps=3,inventory_fn=_inventory)
    invalid=_fake_case('encounter');invalid['structural_topology']='4x2'
    with pytest.raises(ValueError,match='FIXED_TRAIN_CONTRACT'):
        m._train([invalid,_fake_case('allocation')],source13.SourceConditionedScorer(4),
                 steps=3,inventory_fn=_inventory)


def test_fresh_cohorts_prompt_disjoint_and_sealed_excluded(monkeypatch):
    seen=[{'prompt':'train'}]
    d20={'a':[{'prompt':'dev20'}]};d21={'b':[{'prompt':'dev21'}]};d22={'c':[{'prompt':'dev22'}]}
    def independent(batches,*,limit,seed):
        assert limit==320 and seed==460028
        assert {'train','dev20','dev21','dev22'}<={r['prompt'] for rows in batches for r in rows}
        return [{'prompt':f'i{i}','split':'dev'} for i in range(limit)]
    def topology(batches,*,limit,seed):
        assert limit==128 and seed==460029
        return [{'prompt':f't{i}','split':'dev',
                 'structural_topology':('2x4','4x2','4x4')[i%3]} for i in range(limit)]
    monkeypatch.setattr(m.general16,'independent_rows',independent)
    monkeypatch.setattr(m.general16,'heldout_topology_rows',topology)
    cohorts=m.fresh_cohorts(seen,d20,d21,d22)
    assert [len(v) for v in cohorts.values()]==[320,128]
    monkeypatch.setattr(m.general16,'independent_rows',lambda *args,**kwargs:
                        [{'prompt':'train','split':'dev'}]*320)
    with pytest.raises(ValueError,match='DEV_COHORT_CONTRACT'):
        m.fresh_cohorts(seen,d20,d21,d22)


def test_parent22_rejects_bad_schema_before_weights(tmp_path):
    path=tmp_path/'parent22';path.mkdir()
    (path/m.parent22.REPORT).write_text('{"schema":"bogus"}')
    with pytest.raises(ValueError,match='PARENT_0022_CONTRACT'):
        m._verify_parent22(path,{}, {},'bad')


def test_aggregate_and_gate_separate_ranking_from_graph_f1(tmp_path):
    def record(split):
        return {'group':split,'family':'encounter','index':1,
            'density_bucket':'crowded_49_128','structural_topology':'native',
            'gold_total':2,'reachable_gold':2,
            'configurations':{a:{'correct':1,'missing':1,'false_positive':1,'exact':False}
                              for a in m.ARMS},
            'competition':{a:{'gold_with_competing_negatives':2,
                              'gold_not_ahead_of_hardest_negative':1} for a in m.ARMS}}
    groups={s:[record(s)] for s in m.SPLITS}
    f1=tmp_path/'a.gz';f2=tmp_path/'b.gz'
    m._save_cases(groups,f1);m._save_cases(groups,f2)
    assert f1.read_bytes()==f2.read_bytes()
    with gzip.open(f1,'rt',encoding='utf-8') as z:
        assert len(z.readlines())==len(m.SPLITS)
    result=m.aggregate(groups[m.NEW[0]])
    assert result['reachable_gold']==2
    assert not m.gates(result)['context_f1_over_capacity_only']
    assert not m.gates(result)['context_rank_failures_below_capacity_only']


def test_run_immutable_output_and_fixed_steps(tmp_path):
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,output=tmp_path,g1_validation_report=None,g2_run=None,
            selection_run=None,discrimination_run=None,relational_run=None,
            density_run=None,consistency_run=None,context_run=None,learned_run=None,
            recall_run=None,crowded_run=None,robustness_run=None,diagnosis_run=None,
            source_run=None,specialization_run=None,routing_robustness_run=None,
            general_run=None,ceiling_run=None,context18_run=None,train19_run=None,
            ceiling20_run=None,ceiling21_run=None,competition22_run=None)
    with pytest.raises(ValueError,match='FIXED_PROTOCOL'):
        m.run(root=tmp_path,output=tmp_path/'new',g1_validation_report=None,g2_run=None,
            selection_run=None,discrimination_run=None,relational_run=None,
            density_run=None,consistency_run=None,context_run=None,learned_run=None,
            recall_run=None,crowded_run=None,robustness_run=None,diagnosis_run=None,
            source_run=None,specialization_run=None,routing_robustness_run=None,
            general_run=None,ceiling_run=None,context18_run=None,train19_run=None,
            ceiling20_run=None,ceiling21_run=None,competition22_run=None,steps=2)


def test_controls_loaded_only_from_verified_0022_weights(tmp_path):
    from safetensors.torch import save_file
    guard=source13.SourceConditionedScorer(4)
    directory=tmp_path/'parent22';directory.mkdir()
    valid={arm+'.'+key:v.detach().contiguous().clone()
           for arm in m.parent22.ARMS[1:] for key,v in guard.state_dict().items()}
    path=directory/m.parent22.WEIGHTS
    save_file(valid,str(path))
    sha=m.backbone.sha256(path)
    controls=m._load_parent_controls(directory,guard,sha)
    assert set(controls)==set(m.parent22.ARMS[1:])
    assert all(not p.requires_grad for net in controls.values() for p in net.parameters())
    assert all(torch.equal(guard.state_dict()[k],controls['matched_finetune'].state_dict()[k])
               for k in guard.state_dict())
    with pytest.raises(ValueError,match='PARENT_WEIGHT_DRIFT'):
        m._load_parent_controls(directory,guard,'0'*64)
    valid.pop(next(iter(valid)))
    save_file(valid,str(path))
    with pytest.raises(ValueError,match='PARENT_WEIGHT_KEYS'):
        m._load_parent_controls(directory,guard,m.backbone.sha256(path))


def test_full_parent_0022_live_replay_detects_control_drift(monkeypatch):
    anchors=[(0,1,'actor'),(2,3,'action'),(4,5,'action')]
    edges=[(anchors[0],anchors[1],'owns'),(anchors[0],anchors[2],'owns')]
    row={'prompt':'mock','family':'encounter','split':'dev'}
    case={'prompt_sha256':base._sha('mock'),'family':'encounter','anchors':anchors,
          'vectors':torch.randn(3,4),'gold':{edges[0],edges[1]}}
    def inventory(*args,**kwargs):
        assert kwargs['top_k']==24 and kwargs['train_gold'] is False
        x,b=_features(2)
        return {'features':x,'base':b,'groups':[0,0],'edges':edges}
    monkeypatch.setattr(m.source13,'make_inventory',inventory)
    monkeypatch.setattr(m.rel,'full_logits',lambda *args:torch.zeros(1))
    guard=source13.SourceConditionedScorer(4)
    with torch.no_grad(): guard.judge[-1].bias.fill_(10.)
    trained={arm:m.RelationalContextScorer.from_guard(guard) for arm in m.TRAINED}
    nets={m.ARMS[0]:guard,m.ARMS[1]:deepcopy(guard),m.ARMS[2]:deepcopy(guard),**trained}
    found=m._evaluate_case(case,row,None,nets,set(),split=m.REPLAY[0],index=1,device='cpu')
    assert found['reachable_gold']==2
    assert found['configurations'][m.ARMS[0]]['correct']==2
    parent={'prompt_sha256':case['prompt_sha256'],'candidate_count':2,'reachable_gold':2,
            'configurations':dict(zip(m.parent22.ARMS,
                                     [found['configurations'][a] for a in m.ARMS[:3]])),
            'competition':dict(zip(m.parent22.ARMS,
                                   [found['competition'][a] for a in m.ARMS[:3]]))}
    assert m._evaluate_case(case,row,None,nets,set(),split=m.REPLAY[0],index=1,
                            device='cpu',parent_record=parent)==found
    parent['configurations']['matched_finetune']['correct']-=1
    with pytest.raises(ValueError,match='LIVE_REPLAY_DRIFT'):
        m._evaluate_case(case,row,None,nets,set(),split=m.REPLAY[0],index=1,
                         device='cpu',parent_record=parent)
