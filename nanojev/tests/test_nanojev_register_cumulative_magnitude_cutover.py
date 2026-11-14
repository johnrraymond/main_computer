"""Independent magnitude cutover tests, CPU-only."""
import sys
from pathlib import Path
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_register_cumulative_magnitude_cutover as m


def test_thermometer_targets():
    observed=m.cumulative_target(torch.arange(6).reshape(3,2))
    assert observed.shape==(3,2,5)
    assert observed[0,0].tolist()==[0,0,0,0,0]
    assert observed[1,1].tolist()==[1,1,1,0,0]
    assert observed[2,1].tolist()==[1,1,1,1,1]


def test_nested_monotone_gates():
    head=m.CumulativeMagnitude()
    gate,corr=head(torch.randn(32,m.h.INPUT_WIDTH))
    assert (gate[:,:,1:]<=gate[:,:,:-1]).all()
    assert torch.isfinite(corr).all()


def test_joint_gradients():
    net=m.ExecutableNumeracy(op_width=8)
    raw,op,gates,corr=net(torch.randn(4,m.h.INPUT_WIDTH))
    labels,bands,_=m.b.supervision_fast(
        torch.tensor([[300,120],[-8,21],[1400,-200],[0,0]]).numpy(),'cpu')
    loss,parts=m.execution_loss(raw,op,labels,bands,
        ['sym_add','sym_sub','sym_multiply','conditional_gt'],
        gates=gates,correction=corr)
    loss.backward()
    assert net.cumulative.delta.weight.grad.abs().sum()>0
    assert net.cumulative.threshold_logits.weight.grad.abs().sum()>0
    assert net.registers.common_magnitude.weight.grad.abs().sum()>0
    assert net.operations[1].weight.grad.abs().sum()>0
    assert all(torch.isfinite(x) for x in parts.values())


def test_result_only_reaches_correction():
    net=m.ExecutableNumeracy(op_width=8)
    raw,op,gates,corr=net(torch.randn(3,m.h.INPUT_WIDTH))
    labels,bands,_=m.b.supervision_fast(
        torch.tensor([[300,120],[15,5],[-100,-25]]).numpy(),'cpu')
    loss,_=m.execution_loss(raw,op,labels,bands,
        ['sym_add','sym_sub','sym_multiply'],gates=gates,correction=corr,
        threshold_weight=0,op_weight=0,result_weight=0,routed_result_weight=1)
    loss.backward()
    assert net.cumulative.delta.weight.grad.abs().sum()>0
    assert net.operations[1].weight.grad.abs().sum()>0
