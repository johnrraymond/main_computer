"""Numerical boundary contract v1: independent real and complex readouts.

`y == 1` means the entire unit circle is valid for the *encoding* of y.
This is not an assertion that every point on the circle equals 1+0i.

Value encoding: rho=asinh((y-1)/scale), z=exp(rho)*(cos theta+i sin theta).
The learned complex branch directly emits a,b; its angle is not fixed. The
learned real branch emits rho independently. Both receive ground-truth
supervision; agreement alone never certifies correctness.

No LM-head decoding, no oracle during inference, no implicit stop signal.
"""
from __future__ import annotations

import math

SCHEMA = 'nanojev.numeric-complex-contract.v1'


def _torch():
    import torch
    return torch


def target_code(number, scale=64.0):
    torch = _torch()
    if not scale > 0 or not math.isfinite(scale):
        raise ValueError('NUMERIC_INVALID_SCALE')
    y = torch.as_tensor(number, dtype=torch.float64)
    if not bool(torch.isfinite(y).all()):
        raise ValueError('NUMERIC_NONFINITE_TARGET')
    return torch.asinh((y - 1.) / scale)


def log_radius(a, b):
    """Stable log hypot, including large finite a,b. Undefined at the origin."""
    torch = _torch()
    aa=torch.as_tensor(a).double();bb=torch.as_tensor(b).double()
    if not bool((torch.isfinite(aa)&torch.isfinite(bb)).all()):
        raise ValueError('NUMERIC_NONFINITE_COMPLEX')
    return torch.log(torch.hypot(aa,bb).clamp_min(1e-300))


def decode_code(rho,scale=64.0):
    torch = _torch()
    # Both readouts use the same invertible encoding. No hard saturation;
    # inference returns +/-inf on overflow rather than silently faking an answer.
    return 1.+float(scale)*torch.sinh(torch.as_tensor(rho,dtype=torch.float64))


def _numeric_head(hidden,device='cpu'):
    import torch
    from torch import nn
    class NumericalBoundary(nn.Module):
        def __init__(self):
            super().__init__()
            inner=max(16,hidden//4)
            self.direct=nn.Sequential(nn.LayerNorm(hidden),nn.Linear(hidden,inner),nn.GELU(),nn.Linear(inner,1))
            self.complex=nn.Sequential(nn.LayerNorm(hidden),nn.Linear(hidden,inner),nn.GELU(),nn.Linear(inner,2))
            # Begin on unit circle with a nontrivial phase: both components have gradient.
            nn.init.zeros_(self.complex[-1].weight)
            with torch.no_grad():self.complex[-1].bias.copy_(torch.tensor([.8,.6]))
        def forward(self,h,scale=64.):
            h=h.float()
            direct_rho=self.direct(h).squeeze(-1).double()
            ab=self.complex(h).double()
            crho=log_radius(ab[...,0],ab[...,1])
            return {'direct_rho':direct_rho,'a':ab[...,0],'b':ab[...,1],
                    'complex_rho':crho,'direct_number':decode_code(direct_rho,scale),
                    'complex_number':decode_code(crho,scale),
                    'delta_rho':crho-direct_rho,
                    'phase':torch.atan2(ab[...,1],ab[...,0])}
    return NumericalBoundary().to(device)


def loss(outputs, values, scale=64., agreement_weight=0.1):
    import torch
    import torch.nn.functional as F
    target=target_code(values,scale).to(device=outputs['direct_rho'].device)
    direct=F.smooth_l1_loss(outputs['direct_rho'],target)
    complex_loss=F.smooth_l1_loss(outputs['complex_rho'],target)
    # Mild consistency; branches still receive *independent* labels.
    agree=F.smooth_l1_loss(outputs['direct_rho'],outputs['complex_rho'])
    total=direct+complex_loss+agreement_weight*agree
    if not torch.isfinite(total):raise RuntimeError('NUMERIC_NONFINITE_LOSS')
    return total,{'direct':float(direct.detach()),'complex':float(complex_loss.detach()),
                  'agreement':float(agree.detach())}


def features(outputs):
    """Float32 transformable numerical state to attach to a future HybridLink.

    It does not *activate* recursive inference or claim that the readouts are
    correct; the adapter for a later rung must use its own training controls.
    """
    import torch
    vals=('direct_rho','complex_rho','a','b','delta_rho')
    return torch.stack([outputs[k].float() for k in vals],dim=-1)


def diagnostics(outputs, values, scale=64.):
    import torch
    targets=torch.as_tensor(values,dtype=torch.float64,device=outputs['direct_rho'].device)
    truecode=target_code(targets,scale)
    err1=(outputs['direct_rho']-truecode).abs()
    err2=(outputs['complex_rho']-truecode).abs()
    agree=(outputs['delta_rho']).abs()
    n=max(1,targets.numel())
    with torch.no_grad():
        return {'count':targets.numel(),
                'direct_code_mae':round(float(err1.mean()),6),
                'complex_code_mae':round(float(err2.mean()),6),
                'agreement_code_mae':round(float(agree.mean()),6),
                'direct_code_within_0_1':round(float((err1<=0.1).float().mean()),6),
                'complex_code_within_0_1':round(float((err2<=0.1).float().mean()),6),
                'unity_targets':int((targets==1).sum()),
                'complex_unity_radius_error':round(float((outputs['complex_rho'][targets==1]).abs().mean()),6) if (targets==1).any() else None,
                'real_finite':bool(torch.isfinite(outputs['direct_number']).all()),
                'complex_finite':bool(torch.isfinite(outputs['complex_number']).all()),
                'n':n}
