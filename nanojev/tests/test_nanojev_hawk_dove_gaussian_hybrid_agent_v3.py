"""Typed teacher and two-pass self-conditioned numerical HybridAgent v3."""
import copy
import torch
import pytest
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v3 as v3
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v2 as v2
from tools import nanojev_hawk_dove_gaussian_hybrid_agent as prior
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind


def fixture(tmp_path):
    return v2_test_fixture(tmp_path)


def v2_test_fixture(tmp_path):
    from tests.test_nanojev_hawk_dove_gaussian_hybrid_agent_v2 import fixture as src_fixture
    return src_fixture(tmp_path)


def flags(source,output,**kw):
    from tests.test_nanojev_hawk_dove_gaussian_hybrid_agent_v2 import flags as src_flags
    a=src_flags(source,output,**{k:v for k,v in kw.items() if k in ('stop_after','warmup_stop_after','resume')})
    a.bootstrap_steps=kw.get('bootstrap_steps',3)
    a.bootstrap_stop_after=kw.get('bootstrap_stop_after',None)
    a.bootstrap_lr=kw.get('bootstrap_lr',.003)
    a.min_teacher_gain=kw.get('min_teacher_gain',-1.)  # only tiny smoke fixtures
    a.warmup_lr=kw.get('warmup_lr',.002)
    a.lr=kw.get('lr',.0003)
    return a


def test_typed_roles_and_nonoracle_inference(tmp_path):
    _,ref,data=fixture(tmp_path)
    agent=v3.ContextualGaussianHybridAgent(ref)
    x=data['train']['features'][:4]
    assert torch.equal(agent(x,0),ref(x))
    assert agent.teacher_tokens(x).shape==(4,8,ref.hidden)
    assert not torch.equal(agent.teacher_tokens(x)[:,0],agent.teacher_tokens(x)[:,1])
    assert torch.equal(v3.stage_values(x)[:,:4],prior.partial_products(x))
    assert agent._context(x)[0].shape==(4,2,ref.hidden)
    assert agent.teacher_prefix(x,4).shape==(4,6,ref.hidden)
    def forbidden(_):
        raise AssertionError('teacher accessed at inference')
    agent.teacher_tokens=forbidden
    assert agent(x,4).shape==(4,2,ref.bins)


def test_core_and_link_mode_freezing(tmp_path):
    _,ref,_=fixture(tmp_path)
    agent=v3.ContextualGaussianHybridAgent(ref)
    v3.configure_trainable(agent,'bootstrap')
    assert any(p.requires_grad for p in agent.typed.parameters())
    assert all(not p.requires_grad for p in agent.link.parameters())
    v3.configure_trainable(agent,'activation')
    assert all(not p.requires_grad for p in agent.core.parameters())
    assert all(not p.requires_grad for p in agent.answer.parameters())
    assert all(not p.requires_grad for p in agent.typed.parameters())
    assert all(p.requires_grad for p in agent.link.parameters())
    v3.configure_trainable(agent,'joint')
    assert all(p.requires_grad for p in agent.core.parameters())
    assert all(p.requires_grad for p in agent.link.parameters())
    assert all(not p.requires_grad for p in agent.typed.parameters())
    assert all(not p.requires_grad for p in ref.parameters())


def test_actual_two_pass_and_gradient(tmp_path):
    _,ref,data=fixture(tmp_path)
    agent=v3.ContextualGaussianHybridAgent(ref)
    x=data['train']['features'][:4];y=data['train']['numeric'][:4]
    v3.configure_trainable(agent,'joint')
    pred=agent.first_pass(x,4)
    assert pred.shape==(4,4,ref.hidden)
    assert not torch.allclose(pred,agent.teacher_tokens(x)[:,:4])
    a=agent(x,4,supplied_tokens=pred.detach())
    b=agent(x,4)
    assert a.shape==b.shape==(4,2,ref.bins)
    loss,parts=v3.self_conditioned_loss(agent,x,y,((-2500000.,2500000.),)*2,32,2.5,4)
    assert len(parts)==3
    loss.backward()
    for part in ('core','link','answer'):
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in getattr(agent,part).parameters()),part
    assert all(p.grad is None for p in agent.typed.parameters())
    assert all(p.grad is None for p in agent.reference.parameters())


def test_teacher_gate_fails_closed(tmp_path):
    source,_,_=fixture(tmp_path)
    with pytest.raises(RuntimeError,match='TEACHER_INFORMATIVITY_GATE_FAILED'):
        v3.train(flags(source,tmp_path/'reject',min_teacher_gain=1.))
    assert (tmp_path/'reject'/'teacher_gate.json').exists()
    assert not (tmp_path/'reject'/'activation.json').exists()


def test_fixture_end_to_end_and_source_immutability(tmp_path):
    source,_,_=fixture(tmp_path)
    sha=prior.sha256_file(source)
    out=tmp_path/'run'
    result=v3.train(flags(source,out))
    assert prior.sha256_file(source)==sha
    assert result['trained_steps']==4
    assert result['best_step']>0
    assert (out/'teacher_gate.json').exists()
    assert (out/'activation.json').exists()
    assert (out/'latest.pt').exists()
    assert (out/'best.pt').exists()


def test_resume_exact_at_all_three_stages(tmp_path):
    source,_,_=fixture(tmp_path)
    full=v3.train(flags(source,tmp_path/'full'))
    for name,pause in [('bootstrap',{'bootstrap_stop_after':1}),
                       ('activation',{'warmup_stop_after':1}),
                       ('self',{'stop_after':2})]:
        target=tmp_path/name
        result=v3.train(flags(source,target,**pause))
        assert result['status'].startswith('PAUSED_')
        final=v3.train(flags(source,target,resume=True))
        assert final['best_step']==full['best_step']
        a=torch.load(tmp_path/'full'/'latest.pt',weights_only=True)
        b=torch.load(target/'latest.pt',weights_only=True)
        assert a['history']==b['history']
        for key in a['model']:
            assert torch.equal(a['model'][key],b['model'][key]),(name,key)


def test_refuse_changed_checkpoint(tmp_path):
    source,_,_=fixture(tmp_path)
    out=tmp_path/'run'
    v3.train(flags(source,out,bootstrap_stop_after=1))
    checkpoint=torch.load(source,weights_only=True)
    checkpoint['model']['encoder.0.bias'][0]+=1.
    torch.save(checkpoint,source)
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        v3.train(flags(source,out,resume=True))
