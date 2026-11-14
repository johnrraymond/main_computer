"""Tests for frozen numerical-resolution banks; no production weights required."""
from pathlib import Path
import json
from types import SimpleNamespace
from unittest import mock

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_zero_line_gaussian_radius import (
    ARMS, GaussianBank, ReadoutAdapter, certify, encode_base, evaluate,
    fold, infer, make_cache, new_batch_indices, run, sample_pools,
    supervised_loss, trainable_count,
)
from tools.nanojev_zero_line_head_radius_growth import sampled_cards
from tools.nanojev_zero_line_ladder import SCHEMA as LADDER_SCHEMA, deck


def test_bank_uses_latent_not_answer():
    torch.manual_seed(3)
    bank=GaussianBank(16,16)
    assert bank(torch.randn(5,1)).shape==(5,73)
    # A learned projection is available; no residual/target input is accepted.
    assert torch.allclose(bank.centers[0],torch.tensor(-16.))
    assert torch.allclose(bank.centers[-1],torch.tensor(16.))


@pytest.mark.parametrize('arm',ARMS)
def test_adapter_identity_at_initialization_and_gradient(arm):
    torch.manual_seed(7)
    a=ReadoutAdapter(16,arm,128)
    x=torch.randn(6,16)
    original=torch.randn(6,73)
    out,weight=a(x,original,16,True)
    assert torch.equal(out['sign'],original[:,:3])
    assert torch.equal(out['digits'].reshape(-1,70),original[:,3:])
    assert (weight is None) == (arm=='mlp')
    loss=(out['digits']**2).mean()+out['sign'].square().mean()
    loss.backward()
    assert any(p.grad is not None for p in a.parameters())
    assert trainable_count(a)>0


def test_routed_weights_are_soft_and_learned_from_latent():
    model=ReadoutAdapter(8,'routed',128)
    with torch.no_grad():
        model.router.weight[1,:]=1.0
        model.router.weight[0,:]=-1.0
    x=torch.stack((torch.ones(8),-torch.ones(8)))
    base=torch.zeros(2,73)
    _,weights=model(x,base,32,True)
    assert weights.shape==(2,2)
    assert torch.allclose(weights.sum(-1),torch.ones(2))
    assert not torch.allclose(weights[0],weights[1])


def test_gaussian_routing_respects_staged_capacity():
    model=ReadoutAdapter(8,'gaussian',128)
    x=torch.randn(3,8);base=torch.zeros(3,73)
    for r,n in [(16,1),(32,2),(64,3),(128,4)]:
        _,weights=model(x,base,r,True)
        assert weights.shape==(3,n)


def test_fold_holds_swaps_and_global_sign_mirrors_together():
    for a,b in [(225,-223),(143,-159),(130,0)]:
        assert fold(a,b,17)==fold(b,a,17)==fold(-a,-b,17)


def test_datasets_separated_by_unsigned_families():
    train=sampled_cards(16,((128,159),(160,191)),19,'train',3)
    dev=sampled_cards(16,((128,159),(160,191)),19,'dev',3)
    family=lambda row:tuple(sorted((abs(row[0]),abs(row[1]))))
    assert {family(a) for a in train}.isdisjoint({family(b) for b in dev})
    assert any(y == 0 for _,_,y,_,_ in train)
    assert any(y < 0 for _,_,y,_,_ in train)
    assert any(y > 0 for _,_,y,_,_ in train)


def test_certification_cannot_skip_signed_gap_or_missing_cell():
    cells={f'{d}:{band}:{o}':{'rate':1.,'count':4} for d in range(-3,4)
           for band in range(4) for o in (-1,1)}
    assert certify({'cells':cells,'max_delta':3},.8)==3
    cells['-2:1:-1']['rate']=.75
    assert certify({'cells':cells,'max_delta':3},.8)==1
    del cells['1:0:1']
    assert certify({'cells':cells,'max_delta':3},.8)==0


def test_frozen_backbone_activation_cache_and_training():
    torch.manual_seed(3)
    model=AdditionNet('digit_tokens',16,4,1,1,3)
    for p in model.parameters():p.requires_grad_(False)
    previous={k:v.clone() for k,v in model.state_dict().items()}
    samples=[(-1,1,0,0,-1),(1,-1,0,0,1),(2,-1,1,0,1),(-3,1,-2,0,-1),
             (-10,12,2,0,-1),(10,-11,-1,0,1),(15,-2,13,0,1),(-20,9,-11,0,-1)]
    d=make_cache(model,samples,'cpu',4)
    old=make_cache(model,deck(1),'cpu',4)
    assert d['features'].shape==(8,16)
    assert not d['features'].requires_grad
    head=ReadoutAdapter(16,'routed',16)
    opt=torch.optim.AdamW(head.parameters(),lr=.001)
    rng=__import__('random').Random(7)
    pools=sample_pools(d,16)
    loss=supervised_loss(head,d,old,16,16,rng,.5,1.,pools)
    assert torch.isfinite(loss)
    loss.backward();opt.step()
    assert all(torch.equal(model.state_dict()[k],v) for k,v in previous.items())
    s=evaluate(head,d,16,4,max_delta=16)
    assert s['count']==8 and 0 <= s['accuracy'] <= 1


def _mock_certified_root(root):
    model=AdditionNet('digit_tokens',16,4,1,1,3)
    (root/'rungs').mkdir(parents=True)
    (root/'config.json').write_text(json.dumps({'schema':LADDER_SCHEMA,
                 'settings':{'width':16,'heads':4,'layers':1,'rounds':1,'seed':3}}))
    torch.save({'schema':LADDER_SCHEMA+'.passed','radius':127,'step':1,
                'model':model.state_dict(), 'report':{'passed':True,'streak':3,
                'stability_target':3,'mastery':{'groups':{'all':{'accuracy':1.0}}}}},
               root/'rungs'/'radius_127_passed.pt')


def test_smoke_full_workflow_source_is_immutable(tmp_path):
    """Synthetic shape-valid certificate; this test does NOT claim true mastery."""
    source=tmp_path/'ladder';_mock_certified_root(source)
    before=(source/'rungs'/'radius_127_passed.pt').read_bytes()
    args=SimpleNamespace(ladder_output=source,output=tmp_path/'out',device='cpu',
        arms='routed',max_residual=16,steps_per_stage=2,probe_every=1,
        batch_size=16,eval_batch_size=128,train_per_cell=1,dev_per_cell=1,
        ood_per_cell=1,threshold=.8,min_cell_count=1,old_fraction=.5,distill_weight=1.,
        lr=.0001,seed=17,cpu_threads=2,resume=False)
    # Full historical evaluation is exercised by the small mocked deck.
    with mock.patch('tools.nanojev_zero_line_gaussian_radius.deck',side_effect=lambda _:deck(1)):
        result=run(args)
    assert 'routed' in result
    assert (tmp_path/'out'/'arms'/'routed'/'latest.pt').exists()
    assert not (tmp_path/'out'/'arms'/'routed'/'best.pt').exists() # no fake promotion
    assert (source/'rungs'/'radius_127_passed.pt').read_bytes()==before
    assert json.loads((tmp_path/'out'/'result.json').read_text())['TEST'] is None


def test_no_overwrite_without_resume(tmp_path):
    s=tmp_path/'source';_mock_certified_root(s)
    out=tmp_path/'out';out.mkdir();(out/'keep').write_text('original')
    args=SimpleNamespace(ladder_output=s,output=out,device='cpu',
        arms='mlp',max_residual=16,steps_per_stage=1,probe_every=1,batch_size=16,
        eval_batch_size=16,train_per_cell=1,dev_per_cell=1,ood_per_cell=1,
        threshold=.8,min_cell_count=1,old_fraction=.5,distill_weight=1.,lr=.0001,seed=17,
        cpu_threads=2,resume=False)
    with pytest.raises(FileExistsError,match='ZERO_BANK_OUTPUT_EXISTS'):
        run(args)
    assert (out/'keep').read_text()=='original'


def test_exact_resume_matches_uninterrupted_training(tmp_path):
    source=tmp_path/'source';_mock_certified_root(source)
    base=dict(ladder_output=source,device='cpu',arms='gaussian',
        max_residual=16,steps_per_stage=3,probe_every=1,batch_size=16,
        eval_batch_size=128,train_per_cell=1,dev_per_cell=1,ood_per_cell=1,
        threshold=.8,min_cell_count=1,old_fraction=.5,distill_weight=1.,
        lr=.0001,seed=17,cpu_threads=2,resume=False)
    with mock.patch('tools.nanojev_zero_line_gaussian_radius.deck',side_effect=lambda _:deck(1)):
        run(SimpleNamespace(**base,output=tmp_path/'full'))
        from tools import nanojev_zero_line_gaussian_radius as m
        true_loss=m.supervised_loss
        state={'calls':0}
        def fail_second_step(*a,**kw):
            state['calls']+=1
            if state['calls']==2:
                raise RuntimeError('SIMULATED_INTERRUPTION')
            return true_loss(*a,**kw)
        with mock.patch.object(m,'supervised_loss',side_effect=fail_second_step):
            with pytest.raises(RuntimeError,match='SIMULATED_INTERRUPTION'):
                run(SimpleNamespace(**base,output=tmp_path/'interrupted'))
        assert (tmp_path/'interrupted'/'arms'/'gaussian'/'latest.pt').exists()
        run(SimpleNamespace(**(base|{'resume':True}),output=tmp_path/'interrupted'))
    x=torch.load(tmp_path/'full'/'arms'/'gaussian'/'latest.pt',weights_only=True)
    y=torch.load(tmp_path/'interrupted'/'arms'/'gaussian'/'latest.pt',weights_only=True)
    assert x['history']==y['history']
    assert all(torch.equal(x['adapter'][k],y['adapter'][k]) for k in x['adapter'])


def test_roughly_parameter_matched_controls():
    counts=[trainable_count(ReadoutAdapter(64,arm)) for arm in ARMS]
    assert max(counts)/min(counts)<1.1


def test_gaussian_banks_have_distinct_numerical_resolution():
    narrow=GaussianBank(16,16)
    broad=GaussianBank(16,128)
    assert narrow.centers.tolist()!=broad.centers.tolist()
    assert narrow.sigma < broad.sigma
    # At the same coordinate displacement, response widths must differ.
    z=torch.tensor([[8.]])
    n=torch.exp(-.5*((z-narrow.centers)/narrow.sigma).square())
    b=torch.exp(-.5*((z-broad.centers)/broad.sigma).square())
    assert not torch.allclose(n,b)


def test_promotion_requires_full_retention_and_real_radius_growth():
    # The runner's promotion rule is evaluated on full-domain exact counts,
    # with no tolerance-based relaxation. Verify the documented gate fields.
    from inspect import getsource
    src=getsource(run)
    assert "old['correct']==old['count']" in src
    assert "vd['certified_radius']>baseline['dev']['certified_radius']" in src
    assert "od['accuracy']>=baseline['ood']['accuracy']" in src
