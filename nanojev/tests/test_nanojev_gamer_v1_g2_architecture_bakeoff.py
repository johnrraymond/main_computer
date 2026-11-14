"""Architecture bake-off: genuinely distinct models, shared evidence, fixed gates."""
from copy import deepcopy
import json
import gzip

import pytest
import torch

from tools import nanojev_gamer_v1_g2_architecture_bakeoff as m
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


def artificial(width=8):
    prompt='Alice told Bob that Bob warned Carol; Carol warned Bob'
    a=(0,5,'actor');b=(11,14,'actor');c=(20,23,'actor');d=(31,36,'actor')
    edges=[(a,b,'owns'),(a,c,'owns'),(a,d,'owns'),(b,d,'owns')]
    f=torch.randn(4,4*width+source13.EXTRAS+1+20)
    f[:,4*width:4*width+source13.REL_COUNT+1]=torch.tensor([0,1,*([0]*(source13.REL_COUNT-1))],dtype=torch.float32)
    nodes=torch.randn(4,width)
    return {'features':f,'reference':torch.tensor([1.2,1.2,-.4,.1]),
        'nodes':nodes,'src':torch.tensor([0,0,0,1]),'dst':torch.tensor([1,2,3,3]),
        'group':torch.tensor([0,0,0,1]),'edges':edges,
        'gold':{edges[0],edges[1]},'labels':torch.tensor([1.,1.,0.,0.]),
        'frozen_width':width,'prompt':prompt,'all_inventory_edges':4,
        'all_candidate_gold':2}


def test_three_models_are_distinct_and_base_is_unchanged_at_initialization():
    ex=artificial();dim=ex['features'].shape[-1]
    a=m.PredicateArgument(8,dim)
    b=m.DynamicSpanGraph(8,dim)
    c=m.JointGraphDecoder(8,dim)
    assert len({type(x) for x in (a,b,c)})==3
    assert len({sum(p.numel() for p in x.parameters()) for x in (a,b,c)})==3
    for network in (a,b,c):
        assert torch.allclose(network(ex),ex['reference'],atol=1.e-6)
    ex['nodes'][1]=ex['nodes'][1]+3
    assert torch.allclose(a(ex),ex['reference'])
    assert torch.allclose(b(ex),ex['reference'])
    assert torch.allclose(c(ex),ex['reference'])


def test_multi_positive_training_and_gradients():
    ex=artificial();before=ex['reference'].clone()
    for cls in (m.PredicateArgument,m.DynamicSpanGraph,m.JointGraphDecoder):
        net=cls(8,ex['features'].shape[-1]);score=net(ex)
        loss=m.ranking_objective(score,ex)
        if isinstance(net,m.JointGraphDecoder):
            loss=loss+m.joint_cardinality_loss(net,ex,score)
        loss.backward()
        assert torch.isfinite(loss)
        assert any(p.grad is not None and bool(torch.count_nonzero(p.grad)) for p in net.parameters())
        assert torch.equal(before,ex['reference'])


def test_global_decoder_does_not_force_one_destination():
    ex=artificial()
    net=m.JointGraphDecoder(8,ex['features'].shape[-1])
    # The two strongest edges in a group can both be selected.
    selected=net.decode(ex,ex['reference'])
    assert 0 in selected and 1 in selected
    assert 2 not in selected
    # No gold field is used in the decoder.
    alternative=dict(ex,gold=set(),labels=torch.zeros(4))
    assert selected==net.decode(alternative,ex['reference'])


def test_message_passing_depends_on_edges_and_cross_group_neighbors():
    ex=artificial();net=m.DynamicSpanGraph(8,ex['features'].shape[-1])
    with torch.no_grad():
        net.edge[-1].weight.fill_(0.08)
    first=net(ex)
    changed=dict(ex,dst=torch.tensor([2,1,3,1]))
    second=net(changed)
    assert not torch.allclose(first,second)


def test_rank_objective_rejects_unmixed_and_does_not_penalize_positives():
    ex=artificial()
    scores=torch.tensor([1.4,1.5,-1.,0.],requires_grad=True)
    val=m.ranking_objective(scores,ex);val.backward()
    assert scores.grad[0]<0 and scores.grad[1]<0 and scores.grad[2]>0
    empty=dict(ex,labels=torch.ones(4))
    with pytest.raises(ValueError,match='UNMIXED'):
        m.ranking_objective(scores.detach(),empty)


def test_seeded_training_can_run_on_cpu_without_model_mutations():
    ex=artificial();before=deepcopy(ex)
    training=[ex,deepcopy(ex)]*5
    models,log=m.fit_architectures(training,8,ex['features'].shape[-1],
                                  device='cpu',steps=2,seeds=(13,))
    assert set(models[13])==set(m.ARMS)
    assert all(torch.isfinite(torch.tensor(x)) for x in log['13'].values())
    assert torch.equal(before['features'],ex['features'])
    assert torch.equal(before['nodes'],ex['nodes'])
    assert torch.equal(before['reference'],ex['reference'])
    assert all(not param.requires_grad for net in models[13].values() for param in net.parameters())


def test_aggregate_and_gate_requires_consistent_seed_improvement():
    record={'gold_total':4,'candidate_gold':3,'metrics':{},'subgroups':{}}
    for key in (m.REFERENCE,*[f'{arm}_s{seed}' for arm in m.ARMS for seed in (1,2)]):
        record['metrics'][key]={'correct':2,'missing':2,'false_positive':2,
                                 'competing_gold':3,'ranking_failures':1}
    a=m.aggregate([record,record]);assert a['cases']==2
    assert a['offline_deterministic_candidate_oracle_ceiling_f1']==6/7
    assert m.paired_ci([record,record],f'{m.ARMS[0]}_s1',draws=20)==m.paired_ci(
        [record,record],f'{m.ARMS[0]}_s1',draws=20)
    gate=m.promotion_gates(a,a,seeds=(1,2))
    assert not any(v['all_seeds_pass'] for v in gate['arms'].values())
    assert gate['promotion_authorized'] is False


def test_parent28_contract_does_not_accept_unverified_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        m.verify_parent28(tmp_path,{}, {},'x')
    (tmp_path/m.parent28.REPORT).write_text(json.dumps({'schema':'other'}))
    with pytest.raises(ValueError,match='CONTRACT'):
        m.verify_parent28(tmp_path,{}, {},'x')


def test_fixed_cohorts_and_no_promotion():
    assert m.REPLAY==m.parent28.NEW
    assert m.NEW==('new_independent_dev_0029','new_topology_dev_0029')
    assert m.SEEDS==(460038,460039,460040) and m.STEPS==480
    assert m.TOP_K==24 and m.THRESHOLD==m.parent26.THRESHOLD


def test_all_parent_verification_modules_are_imported_for_run():
    # G2-0029 previously failed before training: NameError: ceiling21.
    import builtins
    import inspect
    import symtable

    assert m.ceiling21.verify_parent_0020
    assert m.ceiling21._cohort_rows
    assert m.ceiling21.__name__.endswith('g2_relationship_discrimination_ceiling')
    # Reject any other unresolved global referenced by the full run path.
    table = symtable.symtable(inspect.getsource(m.run), '<run>', 'exec')
    run = next(t for t in table.get_children() if t.get_name() == 'run')
    unresolved = sorted(s.get_name() for s in run.get_symbols()
                        if s.is_global() and not hasattr(m, s.get_name())
                        and not hasattr(builtins, s.get_name()))
    assert unresolved == []
