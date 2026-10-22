"""Read-only proof of the cancellation and clamp-gradient defects; no CUDA or teacher needed."""
from __future__ import annotations
import json
import torch
from tools.nanojev_hawk_dove_gaussian_joint_decoder_21_focus_1k_stable import stable_inverse_asinh

def main():
    cases=[]
    for b,x in ((1_000_000.,1.),(1_000_000.,1.01),(1_000_000.,5.),(1_000_000.,10.),(2_000_000.,1.)):
        base=torch.tensor([b],dtype=torch.float32)
        truth=torch.tensor([x],dtype=torch.float32)
        z=torch.asinh(truth)
        d=z-torch.asinh(base)
        old=base+(torch.sinh(z)-torch.sinh(torch.asinh(base)))
        fixed=stable_inverse_asinh(base,z,d)
        cases.append({'base':b,'target':x,'old':float(old.item()),'fixed':float(fixed.item())})
    z=torch.tensor([22.],requires_grad=True)
    pred=torch.sinh(z.clamp(-20,20))
    grad_old=float(torch.autograd.grad(torch.log1p((pred-10.).abs()/10.),z)[0].item())
    z2=torch.tensor([22.],requires_grad=True)
    fixed=stable_inverse_asinh(torch.tensor([1e6]),z2,torch.tensor([1.]))
    grad_new=float(torch.autograd.grad(torch.log1p((fixed-10.).abs()/10.),z2)[0].item())
    print(json.dumps({'cases':cases,'clamped_numeric_gradient_old':grad_old,
        'clamped_numeric_gradient_repaired':grad_new,'status':'PASS' if max(abs(r['fixed']-r['target']) for r in cases)<.0005 and grad_old==0 and abs(grad_new)>.1 else 'FAIL'},indent=2))
if __name__=='__main__':main()
