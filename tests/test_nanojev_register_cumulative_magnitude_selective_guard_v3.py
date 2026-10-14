import sys
from pathlib import Path
import numpy as np
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_register_cumulative_magnitude_selective_guard_v3 as m


def test_zero_correction_is_exact_identity():
    raw=torch.randn(20,38)
    gates,correction,accept=m.CumulativeMagnitude()(torch.randn(20,m.h.INPUT_WIDTH))
    assert torch.equal(m.cumulative_numbers(raw,gates,correction,accept),torch.from_numpy(m.c.decode(raw.numpy())).float())


def test_gate_shuts_off_correction():
    raw=torch.randn(8,38)
    gates=torch.zeros(8,2,5)
    correction=torch.full((8,2),.5)
    actual=m.cumulative_numbers(raw,gates,correction,torch.zeros(8,2))
    assert torch.equal(actual,torch.tensor(m.c.decode(raw.numpy()),dtype=torch.float32))


def test_acceptance_initialized_closed():
    net=m.CumulativeMagnitude()
    _,_,accept=net(torch.randn(32,m.h.INPUT_WIDTH))
    assert accept.max()<.001


def test_loss_gradients_to_gate_and_delta():
    net=m.ExecutableNumeracy(op_width=8)
    raw,op,gates,corr,accept=net(torch.randn(4,m.h.INPUT_WIDTH))
    values=np.array([[100,20],[-20,5],[400,21],[20,9]])
    labels,bands,_=m.b.supervision_fast(values,'cpu')
    loss,_=m.execution_loss(raw,op,labels,bands,['sym_add','sym_sub','sym_multiply','sym_add'],
        gates=gates,correction=corr,acceptance=accept)
    loss.backward()
    assert net.cumulative.acceptance.weight.grad.abs().sum()>0
    assert net.cumulative.delta.weight.grad.abs().sum()>0


def test_monotone_cumulative_activation():
    gates,_,_=m.CumulativeMagnitude()(torch.randn(30,m.h.INPUT_WIDTH))
    assert torch.all(gates[:,:,1:]<=gates[:,:,:-1])
