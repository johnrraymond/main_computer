"""Operator-control unit tests: integer cancellation, TRAIN-only fit, refusals."""
from __future__ import annotations

from types import SimpleNamespace
import pytest
import torch

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_hybridlink_operator_control as mod


def make_split(n=64, bound=6):
    rng = torch.Generator().manual_seed(124)
    features=[]; truth=[]; rows=[]
    for k in range(n):
        a = (k*98231+17) % (blind.TOTAL+1)
        if k % 11 == 0: a=0
        if k % 13 == 0: a=blind.TOTAL
        if k % 17 == 0: a=500000
        b=blind.TOTAL-a
        payoff = torch.randint(-bound,bound+1,(4,),generator=rng).tolist()
        if k % 17 == 0: payoff=[-2,2,3,-3]  # Exact cancellation at a=b
        if k % 11 == 0: payoff[1]=0
        x=a*payoff[0]+b*payoff[1]
        y=a*payoff[2]+b*payoff[3]
        tier=blind.TIERS[k%len(blind.TIERS)]
        row=[a/blind.TOTAL,b/blind.TOTAL]+[v/bound for v in payoff]+[float(tier==t) for t in blind.TIERS]
        for mask in range(2):
            features.append(row)
            truth.append([x,y])
            rows.append({'tier':tier,'instance':f'i-{k}','mask':mask})
    return {'features':torch.tensor(features,dtype=torch.float32),
            'numeric':torch.tensor(truth,dtype=torch.float32),'rows':rows}


def test_learned_program_exact_recovery_on_synthetic_train_and_dev():
    train=make_split(271)
    dev=make_split(173)
    scale=blind.TOTAL*6
    weights=mod.learn_operator(train,scale)
    program,distance=mod.quantize_weights(weights,.05)
    assert weights.shape==(8,2)
    assert distance<.05
    assert program.abs().sum().item()==4
    results=mod.predict_symbolic(dev['features'],program,6)
    assert torch.equal(results,dev['numeric'].double())
    report=mod.metrics(results,dev,scale,.05,.01)
    assert report['strict']['min_tier_joint5']==1.0
    assert report['strata']['exact_zero']['instances']>0


def test_near_zero_and_exact_zero_keep_integer_precision():
    bound=6
    f=torch.zeros((3,blind.INPUT_DIM),dtype=torch.float32)
    f[0,:6]=torch.tensor([.5,.5,-2/bound,2/bound,3/bound,-3/bound])
    f[1,:6]=torch.tensor([.500001,.499999,-2/bound,2/bound,3/bound,-3/bound])
    f[2,:6]=torch.tensor([0,1,5/bound,0,4/bound,0])
    w=torch.zeros((8,2),dtype=torch.int64)
    w[0,0]=w[5,0]=w[2,1]=w[7,1]=1
    p=mod.predict_symbolic(f,w,bound)
    assert p.tolist()==[[0.,0.],[-4.,6.],[0.,0.]]


def test_discrete_program_rejects_unjustified_snapping():
    w=torch.zeros((8,2),dtype=torch.float64)
    w[0,0]=.89
    with pytest.raises(RuntimeError,match='OPERATOR_DISCRETE_HYPOTHESIS_UNSUPPORTED'):
        mod.quantize_weights(w,.05)


def test_malformed_visible_numbers_refused():
    f=torch.zeros((1,blind.INPUT_DIM))
    f[0,0]=.6;f[0,1]=.6
    with pytest.raises(RuntimeError,match='COUNT_RECONSTRUCTION_FAILED'):
        mod.visible_integers(f,6)


def test_bad_feature_shape_refused():
    with pytest.raises(ValueError,match='BAD_FEATURE_SHAPE'):
        mod.design(torch.ones(2,5))


def test_payoff_intervention_affects_predictions():
    ds=make_split(61)
    weights=mod.quantize_weights(mod.learn_operator(ds,blind.TOTAL*6),.05)[0]
    report=mod.intervention_test(ds,weights,6,1.)
    assert report['changed_fraction']>.3


def test_isolated_run_matches_real_blind_dataset_and_refuses_overwrite(tmp_path):
    import argparse
    from tools import nanojev_hawk_dove_hybridlink_sequence as base
    args=argparse.Namespace(seed=101,banks=16,grid_bins=32,hidden=16,
        target_sigma_bins=2.5,train_matrices=12,dev_matrices=4,test_matrices=4,
        counts_per_matrix=6,masks=2,payoff_bound=2,steps=2,probe_every=1,
        batch_size=16,lr=.003,min_lr=.00003,tolerance=.05,min_rate=.8)
    data,domain,manifest=blind._dataset(args,torch.device('cpu'))
    source=tmp_path/'immutable_source'/'reference.pt'
    source.parent.mkdir()
    torch.save({'schema':blind.SCHEMA,'identity':blind._identity(args,manifest,domain),
                'model':{}},source)
    before=base.file_sha256(source)
    options=mod.parser().parse_args(['--reference-checkpoint',str(source),
                                     '--output',str(tmp_path/'control')])
    result=mod.run(options)
    assert result['status']=='PASS_CONTROL_ONLY'
    assert result['dev_symbolic_integer']['strict']['min_tier_joint5']==1.
    assert base.file_sha256(source)==before
    assert result['train_symbolic_max_abs_error']==0
    assert (tmp_path/'control'/'result.json').exists()
    with pytest.raises(FileExistsError,match='OVERWRITE_REFUSED'):
        mod.run(options)
