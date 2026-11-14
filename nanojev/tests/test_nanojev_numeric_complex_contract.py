import math
import pytest
import torch
from tools import nanojev_numeric_complex_contract as c

@pytest.mark.parametrize('value',[-2000000,-1000,-1,0,1,2,12,36,10000000])
def test_reversible_real_code(value):
    r=c.target_code(value,64)
    actual=c.decode_code(r,64)
    assert math.isclose(float(actual),value,rel_tol=1e-12,abs_tol=1e-8)


def test_unity_entire_unit_circle_not_just_a1_b0():
    for theta in torch.linspace(-math.pi,math.pi,19):
        a=torch.cos(theta);b=torch.sin(theta)
        rho=c.log_radius(a,b)
        assert abs(float(rho))<1e-6
        assert math.isclose(float(c.decode_code(rho)),1.,abs_tol=1e-5)


def test_zero_negative_and_large_are_not_collapsed():
    codes=c.target_code([-1.,0.,1.,2.,10000000.])
    assert codes.tolist()==sorted(codes.tolist())
    assert abs(float(codes[2]))<1e-14
    assert float(codes[-1])>float(codes[3])


def test_complex_and_direct_are_trained_independently():
    torch.manual_seed(33)
    model=c._numeric_head(16)
    x=torch.randn(8,16,requires_grad=True)
    outputs=model(x)
    # Complex pair starts on a nontrivial unit-circle point but is learnable.
    assert torch.allclose(outputs['a'],torch.full((8,),.8,dtype=torch.float64))
    assert torch.allclose(outputs['b'],torch.full((8,),.6,dtype=torch.float64))
    y=[1,2,-3,12,-64,100000,0,1]
    l,components=c.loss(outputs,y)
    assert torch.isfinite(l) and components['direct']>=0 and components['complex']>=0
    l.backward()
    assert x.grad is not None and x.grad.abs().sum()>0
    assert model.direct[-1].weight.grad is not None
    assert model.complex[-1].weight.grad is not None
    assert model.complex[-1].weight.grad.abs().sum()>0
    data=c.features(outputs)
    assert data.shape==(8,5) and data.dtype==torch.float32
    met=c.diagnostics(outputs,y)
    assert met['unity_targets']==2 and met['count']==8


def test_nonfinite_values_and_invalid_scale_rejected():
    for y in [float('nan'),float('inf')]:
        with pytest.raises(ValueError,match='NONFINITE'):c.target_code(y)
    with pytest.raises(ValueError,match='INVALID_SCALE'):c.target_code(3,0)
    with pytest.raises(ValueError,match='NONFINITE'):c.log_radius(float('inf'),1)
