import sys
from pathlib import Path
import numpy as np
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_register_cumulative_magnitude_guarded_cutover as m

def test_identity_forward():
    raw=torch.randn(17,38,requires_grad=True)
    gates,corr=m.CumulativeMagnitude()(torch.randn(17,m.h.INPUT_WIDTH))
    out=m.cumulative_numbers(raw,gates,corr)
    expected=m.c.decode(raw.detach().numpy())
    assert np.array_equal(out.detach().numpy(),expected.astype(np.float32))

def test_bound_sign_and_zero():
    base=torch.tensor([[100.,-20.],[0.,-200.]])
    correction=torch.tensor([[.5,-.5],[.3,.1]])
    class Dummy: pass
    original=m.source_decoder_straight_through
    try:
        m.source_decoder_straight_through=lambda _:base
        out=m.cumulative_numbers(torch.zeros(2,38),None,correction)
    finally:
        m.source_decoder_straight_through=original
    assert torch.equal(torch.sign(out),torch.sign(base))
    assert out[1,0]==0
    assert torch.all(out.abs()<=base.abs()*torch.exp(torch.tensor(.5))+1e-5)

def test_result_gradient_correction_and_operation():
    net=m.ExecutableNumeracy(op_width=8)
    raw,op,gates,corr=net(torch.randn(4,m.h.INPUT_WIDTH))
    nums=np.asarray([[100,20],[-20,5],[400,21],[20,9]])
    labels,bands,_=m.b.supervision_fast(nums,'cpu')
    loss,_=m.execution_loss(raw,op,labels,bands,
        ['sym_add','sym_sub','sym_multiply','sym_add'],gates=gates,correction=corr,
        threshold_weight=0.,op_weight=0.,result_weight=0.,routed_result_weight=1.)
    loss.backward()
    assert net.cumulative.delta.weight.grad.abs().sum()>0
    assert net.operations[1].weight.grad.abs().sum()>0

def test_thermometer_monotone():
    net=m.CumulativeMagnitude()
    gates,_=net(torch.randn(32,m.h.INPUT_WIDTH))
    assert torch.all(gates[:,:,1:]<=gates[:,:,:-1])
