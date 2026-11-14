"""Grounded Phase 1/2 numerical HybridAgent regression checks."""
from __future__ import annotations
import argparse
from pathlib import Path
import pytest
import torch
from tools import nanojev_hawk_dove_gaussian_hybrid_agent as hybrid
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind


def fixture_reference(tmp_path, *, seed=101):
    args=argparse.Namespace(seed=seed,banks=16,grid_bins=32,hidden=16,
        target_sigma_bins=2.5,train_matrices=3,dev_matrices=2,test_matrices=2,
        counts_per_matrix=3,masks=1,payoff_bound=2,steps=2,probe_every=1,
        batch_size=16,lr=.003,min_lr=.00003,tolerance=.05,min_rate=.8)
    dataset, domain, manifest=blind._dataset(args,torch.device('cpu'))
    torch.manual_seed(seed)
    ref=blind.BlindDensityNetwork(args.banks,args.grid_bins,seed,args.hidden)
    ident=blind._identity(args,manifest,domain)
    path=tmp_path/'source.pt'
    torch.save({'schema':blind.SCHEMA,'identity':ident,'step':2,'model':ref.state_dict()},path)
    return path,ref,dataset


def flags(source,output,**kw):
    params={'reference_checkpoint':source,'output':output,'device':'cpu',
         'resume':False,'stop_after':None,'warmup_stop_after':None,'warmup_steps':3,'steps':4,'probe_every':2,
         'batch_size':16,'eval_batch_size':64,'seed':123,'teacher_scale':.35,
         'warmup_lr':.002,'lr':.0003,'tolerance':.05,'cpu_threads':1}
    params.update(kw)
    return argparse.Namespace(**params)


def test_zero_step_exact_and_original_frozen(tmp_path):
    source,base,data=fixture_reference(tmp_path)
    model=hybrid.GaussianHybridAgent(base,seed=123)
    x=data['dev']['features']
    assert torch.equal(model(x,0),base(x))
    assert all(not p.requires_grad for p in model.reference.parameters())
    assert model(x,1).shape==base(x).shape
    assert torch.equal(model(x,8,'disabled'),base(x))
    assert model(x,0).shape==(len(x),2,32)


def test_arithmetic_targets_come_from_visible_fields():
    x=torch.zeros((2,blind.INPUT_DIM),dtype=torch.float32)
    x[0,:6]=torch.tensor([.25,.75,1.,2.,-1.,-2.])
    expected=torch.tensor([[.25,1.5,-.25,-1.5],[0.,0.,0.,0.]])
    assert torch.allclose(hybrid.partial_products(x),expected)
    with pytest.raises(ValueError,match='DIMENSION'):
        hybrid.partial_products(x[:,:4])


def test_self_feedback_gradient_through_multiple_steps(tmp_path):
    _,base,data=fixture_reference(tmp_path)
    m=hybrid.GaussianHybridAgent(base,seed=123)
    m.link.net[-1].weight.data.normal_(mean=0,std=.01)
    x=data['train']['features'][:8]
    d=m(x,depth=4)
    loss=(d[:,:,:8]**2).sum()
    loss.backward()
    assert any(p.grad is not None and float(p.grad.abs().sum())>0 for p in m.link.parameters())
    assert all(p.grad is None for p in m.reference.parameters())


def test_curriculum_and_feedback_controls(tmp_path):
    _,base,data=fixture_reference(tmp_path)
    m=hybrid.GaussianHybridAgent(base,seed=123)
    assert [hybrid.phase_depth(i,8) for i in range(1,9)]==[1,1,2,2,4,4,8,8]
    with torch.no_grad():
        m.link.net[-1].bias.fill_(.35)
        m.link.net[-1].weight.normal_(mean=0,std=.05)
        x=data['dev']['features'][:8]
        assert torch.equal(m(x,8,'disabled'),base(x))
        assert not torch.equal(m(x,1),base(x))
        assert not torch.equal(m(x,1,'shuffled'),m(x,1)) or torch.equal(x,x[0].expand_as(x))
    with pytest.raises(ValueError,match='INVALID_DEPTH'):
        m(x,65)


def test_cpu_end_to_end_and_sources_immutable(tmp_path):
    source,_,_=fixture_reference(tmp_path)
    before=hybrid.sha256_file(source)
    report=hybrid.train(flags(source,tmp_path/'run'))
    assert hybrid.sha256_file(source)==before
    assert report['zero_step_parity']=='PASS'
    assert report['activation_gate']=='PASS'
    assert report['best_step']>0 and report['best_depth'] in (1,2,4,8)
    assert report['TEST_used_for_training_or_promotion'] is False
    assert (tmp_path/'run'/'activation.pt').exists()
    assert (tmp_path/'run'/'latest.pt').exists()
    assert (tmp_path/'run'/'best.pt').exists()


def test_exact_restart_phase2(tmp_path):
    source,_,_=fixture_reference(tmp_path)
    full=hybrid.train(flags(source,tmp_path/'full',steps=4,probe_every=2))
    pause=hybrid.train(flags(source,tmp_path/'split',steps=4,probe_every=2,stop_after=2))
    assert pause['status']=='PAUSED_SELF_CONDITIONED'
    assert not (tmp_path/'split'/'report.json').exists()
    resumed=hybrid.train(flags(source,tmp_path/'split',steps=4,probe_every=2,resume=True))
    assert resumed['trained_steps']==full['trained_steps']==4
    assert resumed['best_depth']==full['best_depth']
    assert resumed['best_step']==full['best_step']
    assert resumed['selected_DEV_min_joint5']==full['selected_DEV_min_joint5']
    a=torch.load(tmp_path/'full'/'latest.pt',weights_only=True)
    b=torch.load(tmp_path/'split'/'latest.pt',weights_only=True)
    assert a['history']==b['history']
    assert torch.equal(a['sampler_rng'],b['sampler_rng'])
    for key in a['link']:
        assert torch.equal(a['link'][key],b['link'][key]), key


def test_exact_restart_activation(tmp_path):
    source,_,_=fixture_reference(tmp_path)
    full=hybrid.train(flags(source,tmp_path/'full',steps=2,probe_every=2))
    pause=hybrid.train(flags(source,tmp_path/'split',steps=2,probe_every=2,
                              warmup_stop_after=1))
    assert pause['status']=='PAUSED_ACTIVATION'
    resumed=hybrid.train(flags(source,tmp_path/'split',steps=2,probe_every=2,resume=True))
    assert resumed['best_depth']==full['best_depth']
    a=torch.load(tmp_path/'full'/'latest.pt',weights_only=True)
    b=torch.load(tmp_path/'split'/'latest.pt',weights_only=True)
    assert a['history']==b['history']
    for key in a['link']:
        assert torch.equal(a['link'][key],b['link'][key])


def test_fail_closed_on_source_mutation(tmp_path):
    source,_,_=fixture_reference(tmp_path)
    hybrid.train(flags(source,tmp_path/'run',steps=4,stop_after=2))
    ck=torch.load(source,weights_only=True)
    ck['model']['encoder.0.bias'][0]+=1
    torch.save(ck,source)
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        hybrid.train(flags(source,tmp_path/'run',steps=4,resume=True))


def test_no_overwrite(tmp_path):
    source,_,_=fixture_reference(tmp_path)
    hybrid.train(flags(source,tmp_path/'run',steps=2))
    with pytest.raises(FileExistsError,match='OVERWRITE'):
        hybrid.train(flags(source,tmp_path/'run',steps=2))
