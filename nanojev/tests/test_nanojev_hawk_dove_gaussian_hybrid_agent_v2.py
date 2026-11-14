"""Causal-memory HybridAgent v2 regression and restart tests."""
from __future__ import annotations
import argparse
import copy
import pytest
import torch
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v2 as v2
from tools import nanojev_hawk_dove_gaussian_hybrid_agent as old
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind


def fixture(tmp_path):
    args=argparse.Namespace(seed=101,banks=16,grid_bins=32,hidden=16,
        target_sigma_bins=2.5,train_matrices=3,dev_matrices=2,test_matrices=2,
        counts_per_matrix=3,masks=1,payoff_bound=2,steps=2,probe_every=1,
        batch_size=16,lr=.003,min_lr=.00003,tolerance=.05,min_rate=.8)
    data,domain,manifest=blind._dataset(args,torch.device('cpu'))
    torch.manual_seed(args.seed)
    ref=blind.BlindDensityNetwork(args.banks,args.grid_bins,args.seed,args.hidden)
    path=tmp_path/'source.pt'
    torch.save({'schema':blind.SCHEMA,'identity':blind._identity(args,manifest,domain),
                'model':ref.state_dict()},path)
    return path,ref,data


def flags(source,output,**kw):
    params={'reference_checkpoint':source,'output':output,'device':'cpu',
            'resume':False,'stop_after':None,'warmup_stop_after':None,
            'warmup_steps':3,'steps':4,'probe_every':2,'batch_size':16,
            'eval_batch_size':64,'seed':123,'heads':4,'layers':1,
            'warmup_lr':.002,'lr':.0003,'tolerance':.05,'cpu_threads':1}
    params.update(kw)
    return argparse.Namespace(**params)


def test_zero_step_and_stage_targets(tmp_path):
    _,ref,data=fixture(tmp_path)
    agent=v2.ContextualGaussianHybridAgent(ref)
    x=data['train']['features'][:5]
    assert torch.equal(agent(x,0),ref(x))
    assert torch.equal(agent(x,0,'disabled'),ref(x))
    assert all(not p.requires_grad for p in agent.reference.parameters())
    stages=v2.stage_values(x)
    pp=old.partial_products(x)
    assert torch.equal(stages[:,:4],pp)
    assert torch.allclose(stages[:,4],pp[:,0]+pp[:,1])
    assert torch.allclose(stages[:,5],pp[:,2]+pp[:,3])
    assert agent.teacher_tokens(x).shape==(5,8,16)
    assert not torch.equal(agent.teacher_tokens(x)[:,0],agent.teacher_tokens(x)[:,1])


def test_prefix_attention_is_causal_and_order_sensitive(tmp_path):
    _,ref,data=fixture(tmp_path)
    agent=v2.ContextualGaussianHybridAgent(ref)
    x=data['train']['features'][:5]
    context,_=agent._context(x)
    tokens=torch.cat([context,agent.teacher_tokens(x)[:,:3]],dim=1)
    before=agent.core(tokens[:,:2])
    modified=tokens.clone();modified[:,2:]+=10*torch.randn_like(modified[:,2:])
    assert torch.allclose(before,agent.core(modified[:,:2]),rtol=0,atol=0)
    orig=agent.core(tokens)
    swapped=tokens.clone();swapped[:,1],swapped[:,2]=tokens[:,2].clone(),tokens[:,1].clone()
    assert not torch.allclose(orig,agent.core(swapped))


def test_generated_feedback_causally_changes_output(tmp_path):
    _,ref,data=fixture(tmp_path)
    agent=v2.ContextualGaussianHybridAgent(ref)
    with torch.no_grad():
        agent.hidden_update.weight.normal_(0,.03)
    x=data['train']['features'][:5]
    normal=agent(x,4)
    assert torch.equal(agent(x,4,'disabled'),ref(x))
    assert not torch.equal(normal,agent(x,4,'zero_tokens'))
    assert not torch.equal(normal,agent(x,4,'shuffled'))
    assert not torch.equal(normal,agent(x,4,'frozen_first'))


def test_joint_gradient_across_multiple_self_generated_vectors(tmp_path):
    _,ref,data=fixture(tmp_path)
    agent=v2.ContextualGaussianHybridAgent(ref)
    x=data['train']['features'][:5]
    loss,_=v2.self_conditioned_loss(agent,x,data['train']['numeric'][:5],
                                     ((-2500000,2500000),)*2,32,2.5,depth=4)
    loss.backward()
    for name,component in [('core',agent.core),('link',agent.link),
                           ('update',agent.hidden_update),('probe',agent.stage_probe)]:
        assert any(p.grad is not None and p.grad.abs().sum()>0 for p in component.parameters()),name
    assert all(p.grad is None for p in agent.reference.parameters())


def test_cpu_end_to_end_no_checkpoint_mutation(tmp_path):
    source,_,_=fixture(tmp_path)
    before=old.sha256_file(source)
    result=v2.train(flags(source,tmp_path/'run'))
    assert old.sha256_file(source)==before
    assert result['trained_steps']==4
    assert result['best_step']>0
    assert 'TEST_hybrid' in result
    assert (tmp_path/'run'/'activation.json').exists()
    assert (tmp_path/'run'/'best.pt').exists()
    assert (tmp_path/'run'/'latest.pt').exists()


def test_resume_exact_cpu(tmp_path):
    source,_,_=fixture(tmp_path)
    full=v2.train(flags(source,tmp_path/'full'))
    partial=v2.train(flags(source,tmp_path/'split',stop_after=2))
    assert partial['status']=='PAUSED_SELF'
    result=v2.train(flags(source,tmp_path/'split',resume=True))
    assert result['best_step']==full['best_step']
    assert result['DEV_hybrid']==full['DEV_hybrid']
    a=torch.load(tmp_path/'full'/'latest.pt',weights_only=True)
    b=torch.load(tmp_path/'split'/'latest.pt',weights_only=True)
    assert a['history']==b['history']
    for key in a['model']:
        assert torch.equal(a['model'][key],b['model'][key]),key


def test_resume_warmup_exact_cpu(tmp_path):
    source,_,_=fixture(tmp_path)
    v2.train(flags(source,tmp_path/'full'))
    partial=v2.train(flags(source,tmp_path/'split',warmup_stop_after=1))
    assert partial['status']=='PAUSED_ACTIVATION'
    v2.train(flags(source,tmp_path/'split',resume=True))
    a=torch.load(tmp_path/'full'/'latest.pt',weights_only=True)
    b=torch.load(tmp_path/'split'/'latest.pt',weights_only=True)
    assert a['history']==b['history']
    for key in a['model']:
        assert torch.equal(a['model'][key],b['model'][key]),key


def test_fail_closed_on_mutated_source(tmp_path):
    source,_,_=fixture(tmp_path)
    v2.train(flags(source,tmp_path/'run',stop_after=2))
    state=torch.load(source,weights_only=True)
    state['model']['encoder.0.bias'][0]+=1
    torch.save(state,source)
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        v2.train(flags(source,tmp_path/'run',resume=True))


def test_no_overwrite(tmp_path):
    source,_,_=fixture(tmp_path)
    v2.train(flags(source,tmp_path/'run'))
    with pytest.raises(FileExistsError,match='OVERWRITE'):
        v2.train(flags(source,tmp_path/'run'))
