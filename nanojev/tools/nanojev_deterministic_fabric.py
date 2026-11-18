"""Oracle-constructed numerical defaults for GAMER trainable heads.

No PRNG is consulted to construct parameters. Meta-device constructors avoid even
transient random sampling by torch module __init__ implementations. Deliberately
specified low-entropy null priors and dyadic coordinates are defaults, not claims
that untrained neural semantics are correct. Source Qwen/LoRA weights are FROZEN,
not retroactively made deterministic by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

import torch

SCHEMA = 'nanojev.gamer.oracle-default-fabric.v1'
VERSION = 'dyadic-dct-null-v1'


def vdc(index: int, base: int = 2) -> float:
    """Base-b van der Corput low-discrepancy coordinate, never a random number."""
    if not isinstance(index,int) or index < 0 or base < 2:
        raise ValueError('FABRIC_VDC_ARGUMENT')
    result,denom=0.0,1.0
    while index:
        index,digit=divmod(index,base)
        denom*=base
        result+=digit/denom
    return result


def dyadic_bits(unit: float, depth: int = 8):
    """Exactly specified half-open binary partitions on [0,1)."""
    if not math.isfinite(unit) or not 0 <= unit < 1 or not 1 <= depth <= 32:
        raise ValueError('FABRIC_DOMAIN')
    residual=float(unit)
    bits=[]
    for _ in range(depth):
        residual*=2
        bit=int(residual >= 1)
        bits.append(bit)
        residual-=bit
    return tuple(bits),residual


def encode_number(number: float, depth: int = 8):
    """Deterministic signed embedding; exact sign and zero are separate anchors.

    The bounded magnitude channel uses |x|/(1+|x|); large-number precision is
    therefore NOT guaranteed and must be measured by the numerical oracle.
    """
    if not math.isfinite(number):
        raise ValueError('FABRIC_NONFINITE_NUMBER')
    sign=-1 if number<0 else 1 if number>0 else 0
    unit=abs(number)/(1+abs(number))
    bits,residual=dyadic_bits(unit,depth)
    phase=tuple(vdc(k+1) for k in range(depth))
    geom=[]
    for k,p in enumerate(phase):
        angle=2*math.pi*(2**k*unit+p)
        geom.extend((math.cos(angle)/(2**k),math.sin(angle)/(2**k)))
    return {'sign':sign,'zero':int(number==0), 'magnitude_coordinate':unit,
        'bits':bits, 'residual':residual, 'geometry':tuple(geom)}


class DyadicDecisionManifold(torch.nn.Module):
    """Executable oracle for binary halves, dyadic residual and smooth geometry.

    The binary channels are piecewise constant; differentiable sinusoid pairs
    have constant derivative vector norm away from finite-precision effects.
    Do NOT interpret the discrete bit channel as a smooth training gradient.
    """
    def __init__(self,depth=8):
        super().__init__()
        if not 1<=depth<=16:
            raise ValueError('FABRIC_DEPTH')
        self.depth=depth
        self.register_buffer('offsets',torch.tensor(
            [vdc(i+1) for i in range(depth)],dtype=torch.float64),persistent=True)

    def forward(self,unit):
        if not bool(torch.isfinite(unit).all()) or not bool(((unit>=0)&(unit<1)).all()):
            raise ValueError('FABRIC_DOMAIN')
        u=unit.to(torch.float64)
        residue=u
        bits=[];continuous=[]
        for k in range(self.depth):
            doubled=2*residue
            bit=(doubled>=1).to(torch.float64)
            residue=doubled-bit
            bits.append(bit)
            theta=2*math.pi*(2**k*u+self.offsets[k])
            amplitude=2.0**(-k)
            continuous.extend([amplitude*torch.cos(theta),amplitude*torch.sin(theta)])
        return {'bits':torch.stack(bits,-1),
                'residual':residue,
                'smooth':torch.stack(continuous,-1),
                'coordinate':u.unsqueeze(-1)}


class DefaultCoupler(torch.nn.Module):
    """Deterministic nonrandom base map plus initially zero learned correction.

    Explicit, reusable interface component. This does not itself establish
    an oracle alignment between separately trained network stages.
    """
    def __init__(self,in_features,out_features):
        super().__init__()
        if in_features<1 or out_features<1:
            raise ValueError('FABRIC_COUPLER_DIMS')
        with torch.device('meta'):
            self.base=torch.nn.Linear(in_features,out_features,bias=False)
            self.correction=torch.nn.Linear(in_features,out_features,bias=False)
        self.to_empty(device='cpu')
        with torch.no_grad():
            if in_features==out_features:
                self.base.weight.copy_(torch.eye(in_features))
            else:
                self.base.weight.copy_(_matrix((out_features,in_features),'coupler.base.weight'))
            self.correction.weight.zero_()

    def forward(self,x):
        return self.base(x)+self.correction(x)


def _phase(name: str) -> int:
    # Role index from the parameter's literal name, not a hash or PRNG.
    # Each new component receives a reproducible position in the phase lattice.
    return sum((index+1)*ord(char) for index,char in enumerate(name))


def _matrix(shape, name):
    rows,cols=shape
    r=torch.arange(rows,dtype=torch.float64).unsqueeze(1)
    c=torch.arange(cols,dtype=torch.float64).unsqueeze(0)
    salt=_phase(name)
    # Deterministic spectral coverage with incommensurate row offsets.
    frequency=1+torch.remainder(r+salt%max(cols,1),max(cols,1))
    offset=2*math.pi*torch.remainder(r*17+salt%997,997)/997
    mat=torch.cos(math.pi*(c+.5)*frequency/max(cols,1)+offset)
    return (mat*math.sqrt(1/max(cols,1))).to(torch.float32)


def _tensor(name, param):
    shape=tuple(param.shape)
    if not shape:
        return torch.zeros(shape,dtype=torch.float32)
    if name.endswith('.bias') or name == 'operand_stop':
        return torch.zeros(shape,dtype=torch.float32)
    # LayerNorm defaults to the identity affine map.
    lowered=name.lower()
    if name.endswith('.weight') and ('norm' in lowered or '.ln' in lowered):
        return torch.ones(shape,dtype=torch.float32)
    if len(shape)==1:
        ix=torch.arange(shape[0],dtype=torch.float64)
        values=0.02*torch.cos(2*math.pi*(ix+.5)/max(shape[0],1)+2*math.pi*vdc(1+_phase(name)%4096))
        return values.float()
    if name.startswith('interface_couplers.') and name.endswith('.base.weight'):
        return torch.eye(shape[0],shape[1],dtype=torch.float32)
    if name.startswith('interface_couplers.') and name.endswith('.delta.weight'):
        return torch.zeros(shape,dtype=torch.float32)
    if len(shape)==2:
        return _matrix(shape,name)
    # Known models have only rank <=2 trainable tensors, but handle
    # other shapes deterministically if architecture extends in the future.
    return _matrix((shape[0],math.prod(shape[1:])),name).reshape(shape)


def fill_defaults(module, *, kind: str):
    """Fill every parameter exactly once with deterministic finite values."""
    if kind not in ('graph_head','triple_set'):
        raise ValueError('FABRIC_UNKNOWN_KIND')
    with torch.no_grad():
        for name,p in module.named_parameters():
            value=_tensor(name,p)
            if tuple(value.shape)!=tuple(p.shape):
                raise ValueError('FABRIC_SHAPE')
            p.copy_(value.to(device=p.device,dtype=p.dtype))
        names=dict(module.named_parameters())
        # Explicit default: abstain rather than hallucinate unlearned labels.
        for name,prior in (
            ('span_classifier.bias',6.0), ('latent_presence.bias',4.0),
            ('binding.bias',4.0),('relationship.bias',4.0),
            ('operator.bias',4.0), ('rel.bias',8.0)):
            if name in names:
                names[name].zero_()
                names[name][0]=prior
        if kind=='triple_set' and 'queries' in names:
            q=names['queries']
            # 64 noninterchangeable query positions in the dyadic fabric.
            for i in range(q.shape[0]):
                t=vdc(i+1)
                j=torch.arange(q.shape[1],device=q.device,dtype=torch.float64)
                q[i].copy_((0.06*torch.cos(2*math.pi*(j+.5)*t)).to(q.dtype))
    for name,p in module.named_parameters():
        if not torch.isfinite(p).all():
            raise ValueError('FABRIC_NONFINITE_DEFAULT:'+name)
    return module


def new_graph_head(*,hidden_size=1024,width=192,layers=1,latent_slots=16,
                   memory_slots=32,max_operands=8,max_span_tokens=24,
                   oracle_coupling=True,device='cpu'):
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    # Meta construction performs no random device computation even when a
    # module's constructor contains torch.randn / xavier_uniform_ internally.
    with torch.device('meta'):
        model=GamerGraphHead(hidden_size=hidden_size,width=width,layers=layers,
            latent_slots=latent_slots,memory_slots=memory_slots,
            max_operands=max_operands,max_span_tokens=max_span_tokens,
            oracle_coupling=oracle_coupling)
    model.to_empty(device='cpu')
    fill_defaults(model,kind='graph_head')
    return model.to(device)


def new_triple_set(*,device='cpu'):
    from tools.nanojev_gamer_v1_g2_literature_bakeoff_0055 import TripleSetPredictor
    with torch.device('meta'):
        model=TripleSetPredictor()
    model.to_empty(device='cpu')
    fill_defaults(model,kind='triple_set')
    return model.to(device)


def state_digest(model):
    h=hashlib.sha256()
    for name,value in sorted(model.state_dict().items()):
        cpu=value.detach().to(device='cpu').contiguous()
        h.update(name.encode()+b'\0')
        h.update(str(tuple(cpu.shape)).encode()+b'\0')
        h.update(cpu.numpy().tobytes())
    return h.hexdigest()


def numerical_oracle(depth=8):
    """Exhaustive oracle for the exact dyadic reference grid, not task accuracy."""
    count=1<<depth
    codes=set()
    halves=[0,0]
    for k in range(count):
        bits,remainder=dyadic_bits((k+.5)/count,depth)
        halves[bits[0]]+=1
        codes.add(bits)
        if not 0<=remainder<1:
            raise AssertionError('FABRIC_RESIDUAL_INVALID')
    for x in (-1024.0,-1.,-1e-8,0.,1e-8,1.,1024.):
        r=encode_number(x,depth)
        if r['sign']!=(-1 if x<0 else 1 if x>0 else 0) or r['zero']!=int(x==0):
            raise AssertionError('FABRIC_SIGN_ORACLE')
        if any(not math.isfinite(t) for t in r['geometry']):
            raise AssertionError('FABRIC_GEOMETRY_NONFINITE')
    if halves!=[count//2,count//2] or len(codes)!=count:
        raise AssertionError('FABRIC_BINARY_COVERAGE')
    return {'grid_count':count,'first_bit_balance':halves,
        'unique_binary_codes':len(codes),'signed_zero_oracle':'PASS',
        'claim_scope':'dyadic_grid_only; not trained-model numerical competence'}


def graph_default_oracle(head,*,hidden_size):
    head.eval()
    with torch.inference_mode():
        output=head(torch.zeros(8,hidden_size),eligible=torch.ones(8,dtype=torch.bool),active_stage='G1')
    logit=output['span_logits']
    if not torch.isfinite(logit).all() or logit.shape[:2]!=(8,head.max_span_tokens):
        raise AssertionError('FABRIC_G1_DEFAULT_INVALID')
    if not torch.all(logit[...,0]>logit[...,1:].amax(-1)):
        raise AssertionError('FABRIC_G1_DEFAULT_NOT_ABSTAINING')
    return {'oracle':'G1 ZERO-INPUT ABSTENTION','status':'PASS',
        'tested_input':'zero hidden, 8 eligible positions; not real Qwen data',
        'max_non_null_margin':float((logit[...,1:].amax(-1)-logit[...,0]).max())}


def set_default_oracle(model):
    model.eval()
    sample={'node':torch.zeros(4,96),'kinds':torch.zeros(4,dtype=torch.long),
            'margin':torch.zeros(4)}
    with torch.inference_mode():
        rel,src,dst=model(sample)
    if (tuple(rel.shape[0:1])!=(64,) or src.shape!=(64,4) or dst.shape!=(64,4)
        or not all(torch.isfinite(t).all() for t in (rel,src,dst))):
        raise AssertionError('FABRIC_G2_DEFAULT_SHAPES')
    if not bool(torch.all(rel[:,0]>rel[:,1:].amax(-1))):
        raise AssertionError('FABRIC_G2_DEFAULT_NOT_ABSTAINING')
    return {'oracle':'G2 ZERO-NODE ABSTENTION','status':'PASS',
        'tested_input':'four zero nodes; no semantic inference claim'}


def contract_ledger():
    return {'stage_oracles':{
        'G1':'exact object spans; actor/action/quantity retention and boundary conflict',
        'G2':'referent binding, actor preservation, graph candidate reachability >= 98%',
        'G3':'directed relationship oracle, endpoint identity and role reversal',
        'G4':'exact numerical sign, zero, operations, operand pointers and carries',
        'G5':'candidate/objective correctness and no unsupported graph admission',
        'G6':'memory conditioned graph consistency and identity',
        'G7':'whole game outcome; exact quantities, graph edges and decisions'},
       'historical_reference':{
        'g2_0062_frozen_step':4900,'g2_0062_f1_percent':81.23,
        'g2_0062_exact_graphs':'4/96',
        'g2_0069_high_lr':'5e-4 regressed under both optimizer states',
        'g2_0069_low_lr':'3e-5 avoided most degradation but no established gain',
        'g2_0068':'alternative loss arms not supported versus equal-step control',
        'candidate_reachability':'prior evidence below 98% threshold; needs repair',
        'inference_labels':'never used',
        'sealed_test':'never inspect for model selection'},
       'default_weight_contract':{
        'binary_partitions':'exactly 50/50 over oracle grid',
        'offsets':'van der Corput deterministic; no sampled random numbers',
        'matrix_basis':'deterministic spectral coordinates',
        'null_prior':'intentional safe abstention when untrained',
        'claim_limit':'not proof of uniform training gradient or semantic accuracy'}}
