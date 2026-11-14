"""Regression tests for G2-0028: label-blind occurrence evidence and matched probes."""
from copy import deepcopy
import pytest,torch
from tools import nanojev_gamer_v1_g2_occurrence_identity_evidence_probe as m
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


def sample():
    prompt='Alice gives 3 to Bob; Bob gives 4 to Alice; Bob gives 5 to Alice'
    actor=(0,5,'actor')
    starts=[i for i in range(len(prompt)) if prompt.startswith('gives',i)]
    actions=[(i,i+5,'action') for i in starts]
    anchors=[actor,*actions]
    edges=[(actor,a,'owns') for a in actions]
    x=torch.randn(3,4*4+source13.EXTRAS);x[1]=x[0]
    inv={'edges':edges,'features':x,'base':torch.tensor([.2,.2,.1]),'groups':[0,0,0]}
    case={'index':1,'anchors':anchors,'vectors':torch.randn(len(anchors),4),
          'gold':{edges[0]},'prompt_sha256':m.base._sha(prompt),'family':'encounter'}
    row={'prompt':prompt,'family':'encounter','structural_topology':'native'}
    return case,row,inv


def test_repeated_text_is_disambiguated_without_changing_frozen_features():
    case,row,inv=sample();before=inv['features'].clone()
    core,pos,st=m.evidence_features(case,row['prompt'],inv,4)
    assert torch.equal(core[0],core[1]) and not torch.equal(pos[0],pos[1])
    assert not torch.equal(st[0],st[1])
    assert torch.equal(inv['features'],before)
    assert pos.shape==(3,10) and st.shape==(3,10)
    assert torch.isfinite(pos).all() and torch.isfinite(st).all()
    case['gold']={inv['edges'][2]}
    others=m.evidence_features(case,row['prompt'],inv,4)
    assert all(torch.equal(a,b) for a,b in zip((core,pos,st),others))


def test_equal_width_and_shuffled_control_preserves_group_marginals():
    case,row,inv=sample()
    core,p,s=m.evidence_features(case,row['prompt'],inv,4)
    arms=m.feature_arms(core,p,s,inv['groups'],case['prompt_sha256'])
    assert len({tuple(v.shape) for v in arms.values()})==1
    assert torch.count_nonzero(arms['frozen_features'][:,-20:])==0
    assert torch.count_nonzero(arms['occurrence_position'][:,-10:])==0
    original=arms['position_and_structure'][:,-20:]
    shuffled=arms['shuffled_evidence_control'][:,-20:]
    assert sorted(map(tuple,original.tolist()))==sorted(map(tuple,shuffled.tolist()))
    again=m.feature_arms(core,p,s,inv['groups'],case['prompt_sha256'])
    assert torch.equal(shuffled,again['shuffled_evidence_control'][:,-20:])


def test_inference_cannot_force_in_train_gold(monkeypatch):
    case,row,inv=sample()
    monkeypatch.setattr(m.rel,'full_logits',lambda *args:torch.zeros(4,4,5))
    def inventory(*args,**kw):
        assert kw['train_gold'] is False
        assert kw['top_k']==24
        return inv
    monkeypatch.setattr(m.source13,'make_inventory',inventory)
    sampled=m.training_groups(case,row,None,set(),device='cpu')
    assert len(sampled)==1 and int(sampled[0][2].sum())==1
    assert all(set(item)==set(m.ARMS) for _,item,_ in sampled)


def test_training_uses_matched_parameters_and_does_not_mutate_source():
    case,row,inv=sample()
    core,p,s=m.evidence_features(case,row['prompt'],inv,4)
    features=m.feature_arms(core,p,s,inv['groups'],case['prompt_sha256'])
    before={a:x.clone() for a,x in features.items()}
    models,loss=m.train_probes([(0,features,torch.tensor([1.,0.,0.]))]*2,
        len(features[m.ARMS[0]][0]),steps=2)
    assert set(models)==set(m.ARMS)
    assert len({sum(p.numel() for p in net.parameters()) for net in models.values()})==1
    assert all(torch.equal(before[a],features[a]) for a in m.ARMS)
    assert all(q.requires_grad is False for net in models.values() for q in net.parameters())
    assert all(value>0 for value in loss.values())


def test_multi_positive_ranking_loss_does_not_penalize_other_gold():
    score=torch.tensor([.5,.3,-.1],requires_grad=True)
    loss=m._loss(score,torch.tensor([1.,1.,0.]));loss.backward()
    assert score.grad[0]<0 and score.grad[1]<0 and score.grad[2]>0
    with pytest.raises(ValueError,match='UNMIXED'):
        m._loss(torch.zeros(3),torch.ones(3))


def test_eval_replay_verifies_frozen_candidate_inventory(monkeypatch):
    case,row,inv=sample()
    monkeypatch.setattr(m.rel,'full_logits',lambda *args:torch.zeros(4,4,5))
    monkeypatch.setattr(m.source13,'make_inventory',lambda *args,**kw:inv)
    core,p,s=m.evidence_features(case,row['prompt'],inv,4)
    feat=m.feature_arms(core,p,s,inv['groups'],case['prompt_sha256'])
    nets={a:m.Probe(feat[a].shape[-1]).eval() for a in m.ARMS}
    class Ref(torch.nn.Module):
        def forward(self,x,b,g,**kw):return torch.zeros(len(x))
    result=m.analyze_case(case,row,None,nets,set(),Ref(),'cpu',m.NEW[0],1)
    assert result['candidate_gold']==1 and set(result['probes'])==set((*m.ARMS,m.REFERENCE))
    old={'group':m.NEW[0],'index':1,'prompt_sha256':case['prompt_sha256'],
         'coverage':{'candidates':3,'candidate_gold':1}}
    m.analyze_case(case,row,None,nets,set(),Ref(),'cpu',m.NEW[0],1,prior=old)
    old['coverage']['candidates']=4
    with pytest.raises(ValueError,match='INVENTORY_DRIFT'):
        m.analyze_case(case,row,None,nets,set(),Ref(),'cpu',m.NEW[0],1,prior=old)


def test_bootstrap_deterministic_and_separates_f1_from_ranking():
    row={'gold_total':2,'candidate_gold':2,'probes':{},'subgroups':{}}
    for a in (*m.ARMS,m.REFERENCE):
        row['probes'][a]={'correct':1,'missing':1,'false_positive':1,
            'competing_gold':2,'ranking_failures':int(a=='frozen_features'),
            'pairwise_comparisons':4,'pairwise_wins':2}
        row['subgroups'][a]={'same_surface':{'count':2,'failures':1}}
    aggregate=m.aggregate([row,row])
    assert aggregate['probes']['frozen_features']['ranking_failure_rate']==.5
    assert aggregate['probes']['position_and_structure']['ranking_failure_rate']==0.
    a=m.paired_bootstrap([row]*3,'position_and_structure','frozen_features',draws=25)
    assert a==m.paired_bootstrap([row]*3,'position_and_structure','frozen_features',draws=25)
    assert a['ranking_failure_rate_delta_CI95']==[-.5,-.5]


def test_generation_contract_and_distinct_parent_cohorts():
    assert m.REPLAY==m.parent27.NEW
    assert m.NEW==('new_independent_dev_0028','new_topology_dev_0028')
    assert m.SEED==460036 and m.STEPS==600 and m.TOP_K==24


def test_bad_predicted_spans_and_bad_vector_width_are_refused():
    case,row,inv=sample();inv['features']=inv['features'][:,:-1]
    with pytest.raises(ValueError,match='FROZEN_WIDTH'):
        m.evidence_features(case,row['prompt'],inv,4)
    case,row,inv=sample();case['anchors'][0]=(-1,5,'actor')
    with pytest.raises(ValueError,match='BAD_PREDICTED_SPANS'):
        m.evidence_features(case,row['prompt'],inv,4)
