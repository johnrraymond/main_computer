"""CPU-only checks for the direct frozen-exponent mantissa experiment."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import torch
import nanojev_altreg_independent_math_tinystories_mantissa_probe_v4 as v4

def test_self_test():
    assert v4.self_test()==0

def test_normalized_decimal_components():
    _,e,m=v4.componentize(torch.tensor([[135.,-27.,1000.]]))
    assert e.tolist()==[[2.,1.,3.]]
    assert torch.allclose(m,torch.tensor([[torch.log10(torch.tensor(1.35)),torch.log10(torch.tensor(2.7)),0.]]),atol=1e-5)

def test_exact_recombination():
    x=torch.tensor([[135.,-27.],[12.,999.]])
    _,_,m=v4.componentize(x)
    assert torch.allclose(v4.reconstruct(x,m),x,rtol=1e-5)

def test_exponent_frozen():
    x=torch.tensor([[135.,-27.]])
    for m in (0.,.15,.5,.9):
        pred=v4.reconstruct(x,torch.full_like(x,m))
        assert torch.equal(v4.componentize(x)[1],v4.componentize(pred)[1])

def test_oracle_exponent_changes_only_exponent():
    x=torch.tensor([[15.,-19.]])
    fraction=torch.tensor([[.2,.3]])
    e=torch.tensor([[2.,3.]])
    out=v4.reconstruct(x,fraction,e)
    assert out[0,0]>100 and out[0,1]<-1000
    assert torch.allclose(v4.componentize(out)[2],fraction,atol=1e-5)

def test_gradients_head_only():
    torch.manual_seed(4)
    head=v4.MantissaHead(12)
    z=torch.randn(8,12)
    parent_coord=torch.randn(8,2)
    frac=head(z,parent_coord,torch.arange(8)%len(v4.base.OP_IDS))
    parent=torch.tensor([[135.,27.]]*8)
    truth=torch.tensor([[140.,28.]]*8)
    l,_=v4.loss_function(frac,truth,v4.reconstruct(parent,frac));l.backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in head.parameters())

def test_score_joint():
    truth=torch.tensor([[100.,200.],[50.,100.]])
    pred=torch.tensor([[103.,207.],[52.,110.]])
    result=v4.score(pred,truth)
    assert result['operand_hits']==3
    assert result['joint_hits']==1
