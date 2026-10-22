#!/usr/bin/env python3
"""Train 16 fixed zero-centered Gaussian banks with one unrestricted numerical decoder.

New experiment, distinct from the finished sequential v2 run. The 16 parallel
banks retain exact one-hot left/right overflow supervision and fixed halved
supports. A shared decoder sees ALL distributions at once and directly learns
the numerical answer in signed-asinh space. It does not select, average, or
replace predictions bank by bank; there are no sequential bank freezes. Upstream feature/Y visibility remains as in v2.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_zero_overflow_16 as prior
from tools import nanojev_hawk_dove_gaussian_joint_decoder_16 as v2
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_correction_ab as store
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_internal_curriculum_master as master

SCHEMA = 'nanojev.hawk-dove.gaussian-joint-decoder-16.v3'
STAGES = prior.STAGES
TIERS = prior.TIERS
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_joint_decoder_16_v3'
RELATIVE_MAX = prior.RELATIVE_MAX
LEGACY_JOINT_SCHEMA = 'nanojev.hawk-dove.gaussian-joint-head-16.v1'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class JointNumericalHead(nn.Module):
    """Unrestricted numerical decoder conditioned on ALL bank distributions.

    Predict a *single* signed-asinh numerical coordinate. The frozen Stage 2
    estimate is a zero-initialized skip connection, NOT a mixture candidate;
    no bank ever overwrites another bank's estimate. Unlike convex mixing,
    the result can lie outside every bank candidate.
    """
    def __init__(self, registers: int, *, count: int = 16, hidden: int = 128):
        super().__init__()
        dim = count * (registers + 2 + 3) + 1
        self.network = nn.Sequential(
            nn.LayerNorm(dim),nn.Linear(dim,hidden),nn.SiLU(),
            nn.Linear(hidden,hidden),nn.SiLU(),nn.Linear(hidden,1))
        nn.init.zeros_(self.network[-1].weight)
        nn.init.zeros_(self.network[-1].bias)

    def forward(self, bank_probs, bank_estimates, radii, base, relative_max):
        inside = bank_probs[:, :, 1:-1].sum(-1)
        signed_overflow = bank_probs[:, :, -1] - bank_probs[:, :, 0]
        normalized_est = bank_estimates / radii.unsqueeze(0)
        payload = torch.cat((bank_probs,normalized_est.unsqueeze(-1),
                             inside.unsqueeze(-1),signed_overflow.unsqueeze(-1)),dim=-1)
        payload = torch.cat((payload.flatten(1),
                             (base / relative_max).clamp(-4.,4.).unsqueeze(-1)),dim=-1)
        # Smoothly bound the coordinate *change*, not its candidate value.
        # +/-32 in asinh space can cross zero and reverse the sign of even a
        # multi-million-unit baseline. Zero at initialization preserves it.
        raw = self.network(payload).squeeze(-1)
        return 32. * torch.tanh(raw / 32.)


class JointGaussianSystem(nn.Module):
    def __init__(self, base_width, stage2, *, relative_max=RELATIVE_MAX, hidden=128):
        super().__init__()
        self.cascade = prior.Cascade(base_width,stage2,relative_max=relative_max)
        self.head = JointNumericalHead(stage2.registers,hidden=hidden)
        self.relative_max = float(relative_max)
        self.register_buffer('radii',torch.tensor(
            [prior.fixed_radius(k,relative_max) for k in STAGES],dtype=torch.float32))

    def forward(self, data):
        base, _, features = self.cascade.bank_input(data)
        per_bank = [self.cascade.banks[str(k)](features) for k in STAGES]
        bank_estimates = torch.stack([entry[0] for entry in per_bank],dim=1)
        bank_probs = torch.stack([entry[1]['probabilities'] for entry in per_bank],dim=1)
        encoded_base = torch.asinh(base.float())
        delta = self.head(bank_probs,bank_estimates,self.radii,base,self.relative_max)
        encoded_prediction = encoded_base + delta
        # Stay in the finite numerical regime. This is far beyond the
        # +/-2-million bank support, so it is NOT a convex-hull constraint.
        bounded_prediction = encoded_prediction.clamp(-20.,20.)
        # Exact identity at step zero (including cases where the parent uses
        # a different floating rounding of sinh(asinh(base))).
        predicted = base + (torch.sinh(bounded_prediction)-torch.sinh(encoded_base))
        return predicted,{'base':base,'decoded_coordinate':encoded_prediction,
                          'decoder_delta':delta,'bank_probs':bank_probs,
                          'bank_estimates':bank_estimates}


def training_objective(system,data,*,small_boost=2.,small_threshold=250000.,
                       gaussian_weight=.10,head_weight=1.,numeric_weight=4.,
                       return_terms=False):
    """Numerical supervision is primary; bank Gaussian CE is auxiliary.

    The main loop additionally caps auxiliary *gradient norm*, since scalar
    weights alone do not control the relative gradient contributions.
    """
    y=data['target'].float()
    predicted,extra=system(data)
    bank_losses=[]
    class_accuracy=[]
    for i,k in enumerate(STAGES):
        bank=system.cascade.banks[str(k)]
        desired=bank.supervised_distribution(y)
        ce=-(desired * extra['bank_probs'][:,i,:].clamp_min(1e-12).log()).sum(-1)
        inside=y.abs()<=bank.radius
        if bool(inside.any()) and bool((~inside).any()):
            bank_loss=.5*(ce[inside].mean()+ce[~inside].mean())
        else:
            bank_loss=ce.mean()
        bank_losses.append(bank_loss)
        # Classify L / interior / R, NOT the most likely specific register.
        actual=torch.where(y < -bank.radius,0,torch.where(y>bank.radius,2,1))
        probs=extra['bank_probs'][:,i,:]
        predicted_class=torch.stack((probs[:,0],probs[:,1:-1].sum(-1),probs[:,-1]),-1).argmax(-1)
        class_accuracy.append((predicted_class==actual).float().mean())
    gaussian=torch.stack(bank_losses).mean()
    weight=torch.where(y.abs()<small_threshold,small_boost,1.)
    weight=weight/weight.sum()
    target_coordinate=torch.asinh(y)
    encoded_error=extra['decoded_coordinate']-target_coordinate
    decoder=(F.smooth_l1_loss(encoded_error,torch.zeros_like(encoded_error),
                              beta=.25,reduction='none')*weight).sum()
    relative_error=(predicted-y).abs()/y.abs().clamp_min(1.)
    numeric=(torch.log1p(relative_error)*weight).sum()
    primary=numeric_weight*numeric+head_weight*decoder
    auxiliary=gaussian_weight*gaussian
    total=primary+auxiliary
    metrics={'gaussian_loss':gaussian.detach(), 'decoder_loss':decoder.detach(),
             'numeric_loss':numeric.detach(), 'primary_loss':primary.detach(),
             'auxiliary_loss_unscaled':auxiliary.detach(),
             'bank_class_accuracy':torch.stack(class_accuracy).mean().detach(),
             'decoder_delta_abs':extra['decoder_delta'].abs().mean().detach()}
    if return_terms:
        return primary,auxiliary,metrics
    return total,metrics


def _grad_norm(grads):
    return torch.sqrt(sum((g.detach().float().square().sum() for g in grads if g is not None),
                          torch.zeros((),device=next(g for g in grads if g is not None).device))) \
           if any(g is not None for g in grads) else torch.tensor(0.)


def numerical_first_backward(system,primary,auxiliary,*,auxiliary_gradient_ratio=.25):
    """Two backward passes, no extra forward: cap auxiliary bank gradients.

    Gaussian gradients are constrained to at most `ratio` times the norm of
    numerical gradients on the bank parameters. Head gradients are strictly
    numerical; Stage 2 remains frozen. Caller performs optimizer.step().
    """
    head=list(system.head.parameters())
    bank=[p for k in STAGES for p in system.cascade.banks[str(k)].parameters()]
    numerical=torch.autograd.grad(primary,head+bank,retain_graph=True,allow_unused=True)
    gaussian=torch.autograd.grad(auxiliary,bank,allow_unused=True)
    head_grads=numerical[:len(head)]
    bank_grads=numerical[len(head):]
    numerical_bank_norm=float(_grad_norm(bank_grads))
    auxiliary_norm=float(_grad_norm(gaussian))
    numerical_head_norm=float(_grad_norm(head_grads))
    if not all(math.isfinite(x) for x in (numerical_bank_norm,auxiliary_norm,numerical_head_norm)):
        raise FloatingPointError('JOINT_NONFINITE_COMPONENT_GRADIENT')
    ratio_cap=0. if numerical_bank_norm<=1e-20 else min(1.,auxiliary_gradient_ratio*numerical_bank_norm/max(auxiliary_norm,1e-20))
    # Use detached scalar ratio: this is gradient balancing, not a new trainable loss.
    for param,grad in zip(head,head_grads):
        param.grad=None if grad is None else grad.detach()
    for param,main_grad,aux_grad in zip(bank,bank_grads,gaussian):
        if main_grad is None and aux_grad is None:
            param.grad=None
        elif main_grad is None:
            param.grad=(ratio_cap*aux_grad).detach()
        elif aux_grad is None:
            param.grad=main_grad.detach()
        else:
            param.grad=(main_grad+ratio_cap*aux_grad).detach()
    # Dot product exposes whether Gaussian supervision conflicts with numerical
    # supervision; component norms quantify *gradient*, not scalar loss values.
    dot=sum((a.detach().float()*b.detach().float()).sum().item()
            for a,b in zip(bank_grads,gaussian) if a is not None and b is not None)
    cosine=dot/(numerical_bank_norm*auxiliary_norm) if numerical_bank_norm>1e-20 and auxiliary_norm>1e-20 else 0.
    return {'numeric_gradient_head_norm':numerical_head_norm,
            'numeric_gradient_bank_norm':numerical_bank_norm,
            'gaussian_gradient_bank_norm_raw':auxiliary_norm,
            'gaussian_gradient_multiplier':ratio_cap,
            'gaussian_gradient_bank_norm_applied':ratio_cap*auxiliary_norm,
            'gaussian_to_numeric_bank_gradient_ratio':ratio_cap*auxiliary_norm/max(numerical_bank_norm,1e-20),
            'numeric_gaussian_bank_gradient_cosine':cosine}


class MagnitudeBalancedSampler:
    """Half ordinary tier-balanced draws, half draws across present magnitude bands."""
    def __init__(self,train,relative_max):
        self.train=train
        self.groups={}
        self.all_pairs=[(t,i) for t in TIERS for i in range(len(train[t]['target']))]
        for t,i in self.all_pairs:
            y=abs(float(train[t]['target'][i]))
            # Group 0: outside widest bank; group N: inside exactly N banks.
            group=sum(y<=relative_max/(2**j) for j in range(len(STAGES)))
            self.groups.setdefault(group,[]).append((t,i))
        self.present=tuple(sorted(self.groups))
        if not self.present:raise ValueError('JOINT_EMPTY_TRAIN_SPLIT')

    def draw(self,seed,step,views):
        if views<2:raise ValueError('JOINT_INVALID_VIEWS')
        rng=random.Random(f'joint/{seed}/{step}/{views}')
        selected=[]
        # Tier-balanced ordinary draws retain exposure to genuine frequency.
        for j in range(views//2):
            tier=TIERS[(step*(views//2)+j)%len(TIERS)]
            selected.append((tier,rng.randrange(len(self.train[tier]['target']))))
        # Rotate through *every populated* magnitude band, including rare
        # small-X interiors. Sampling repeats real examples, never relabels.
        for j in range(views-len(selected)):
            group=self.present[(step*(views-len(selected))+j)%len(self.present)]
            selected.append(rng.choice(self.groups[group]))
        rng.shuffle(selected)
        return {k:torch.stack([self.train[t][k][i] for t,i in selected],dim=0)
                for k in ('features','coarse','target')}


def metrics(truth,pred,threshold):
    small=truth.abs()<threshold
    correct=(pred-truth).abs()/truth.abs().clamp_min(1.) <= .05
    return {'correct':int(correct.sum()),'cases':int(truth.numel()),
            'accuracy_5pct':float(correct.float().mean()),
            'small_n':int(small.sum()),'small_correct':int(correct[small].sum()),
            'small_accuracy_5pct':float(correct[small].float().mean()) if bool(small.any()) else None,
            'small_mae':float((pred[small]-truth[small]).abs().mean()) if bool(small.any()) else None}


def _aggregate_metrics(by_tier,gate_percent):
    n=sum(s['cases'] for s in by_tier.values()); c=sum(s['correct'] for s in by_tier.values())
    small_n=sum(s['small_n'] for s in by_tier.values())
    small_c=sum(s['small_correct'] for s in by_tier.values())
    fails=[t for t,s in by_tier.items() if s['accuracy_5pct']+1e-10<gate_percent/100.]
    return {'accuracy_5pct':c/n,'correct':c,'cases':n,
            'small_accuracy_5pct':small_c/small_n if small_n else None,
            'small_correct':small_c,'small_n':small_n,
            'small_mae':sum(s['small_mae']*s['small_n'] for s in by_tier.values() if s['small_n'])/small_n if small_n else None,
            'all_nine_pass':not fails,'failing_tiers':fails,'per_tier':by_tier}


def _per_bank_stats(system,records):
    out=[]
    for i,k in enumerate(STAGES):
        bank=system.cascade.banks[str(k)]
        radius=bank.radius
        accumulator={'left_n':0,'left_correct':0,'interior_n':0,'interior_correct':0,
                     'interior_numeric_correct_5pct':0,'interior_absolute_error_sum':0.,
                     'right_n':0,'right_correct':0,'predicted_interior':0,'total':0}
        for data,detail in records:
            y=data['target']; prob=detail['bank_probs'][:,i,:]
            classes=torch.stack((prob[:,0],prob[:,1:-1].sum(-1),prob[:,-1]),dim=-1).argmax(-1)
            actual=torch.where(y < -radius,0,torch.where(y>radius,2,1))
            n=int(y.numel());accumulator['total']+=n
            accumulator['predicted_interior']+=int((classes==1).sum())
            for expected,group in ((0,'left'),(1,'interior'),(2,'right')):
                sel=actual==expected; accumulator[group+'_n']+=int(sel.sum())
                accumulator[group+'_correct']+=int(((classes==expected)&sel).sum())
                if expected==1 and bool(sel.any()):
                    estimate=detail['bank_estimates'][:,i]
                    err=(estimate[sel]-y[sel]).abs()
                    accumulator['interior_absolute_error_sum']+=float(err.sum())
                    accumulator['interior_numeric_correct_5pct']+=int((err/y[sel].abs().clamp_min(1.)<=.05).sum())
        a=accumulator;overflow_n=a['left_n']+a['right_n']
        out.append({'stage':k,'bank':i+1,'radius':radius,
                    'interior_n':a['interior_n'],
                    'interior_class_accuracy':a['interior_correct']/a['interior_n'] if a['interior_n'] else None,
                    'interior_numeric_accuracy_5pct':a['interior_numeric_correct_5pct']/a['interior_n'] if a['interior_n'] else None,
                    'interior_mae':a['interior_absolute_error_sum']/a['interior_n'] if a['interior_n'] else None,
                    'left_overflow_n':a['left_n'],
                    'left_overflow_accuracy':a['left_correct']/a['left_n'] if a['left_n'] else None,
                    'right_overflow_n':a['right_n'],
                    'right_overflow_accuracy':a['right_correct']/a['right_n'] if a['right_n'] else None,
                    'overflow_accuracy':(a['left_correct']+a['right_correct'])/overflow_n if overflow_n else None,
                    'predicted_interior_fraction':a['predicted_interior']/a['total']})
    return out


@torch.no_grad()
def _split_probe(system,split,gate_percent,small_threshold,*,compare_base=False):
    by_tier={};base_by_tier={};records=[]
    improved=regressed=outside_hull=0;delta_abs_sum=0.;count=0
    for tier in TIERS:
        data=split[tier];pred,detail=system(data)
        records.append((data,detail))
        y=data['target'];by_tier[tier]=metrics(y,pred,small_threshold)
        if compare_base:
            base_by_tier[tier]=metrics(y,detail['base'],small_threshold)
            before=((detail['base']-y).abs()/y.abs().clamp_min(1.))<=.05
            after=((pred-y).abs()/y.abs().clamp_min(1.))<=.05
            improved+=int((after & ~before).sum());regressed+=int((before & ~after).sum())
            bounds=torch.cat((detail['base'][:,None],detail['bank_estimates']),dim=1)
            outside_hull+=int(((pred<bounds.min(-1).values-1e-4)|
                               (pred>bounds.max(-1).values+1e-4)).sum())
            count+=len(y);delta_abs_sum+=float(detail['decoder_delta'].abs().sum())
    result={'joint':_aggregate_metrics(by_tier,gate_percent),
            'per_bank':_per_bank_stats(system,records)}
    if compare_base:
        result.update({'frozen_stage2':_aggregate_metrics(base_by_tier,gate_percent),
                       'improved_vs_base_cases':improved,'regressed_vs_base_cases':regressed,
                       'decoded_outside_candidate_hull_fraction':outside_hull/count,
                       'mean_abs_decoder_delta':delta_abs_sum/count,
                       'bank_interior_class_accuracy':[row['interior_class_accuracy'] for row in result['per_bank']]})
    return result


@torch.no_grad()
def probe(system,dev,gate_percent,small_threshold,step,train=None):
    system.eval()
    report=_split_probe(system,dev,gate_percent,small_threshold,compare_base=True)
    out={'schema':SCHEMA+'.probe.v1','step':step,
         'joint':report['joint'],'frozen_stage2':report['frozen_stage2'],
         'improved_vs_base_cases':report['improved_vs_base_cases'],
         'regressed_vs_base_cases':report['regressed_vs_base_cases'],
         'decoded_outside_candidate_hull_fraction':report['decoded_outside_candidate_hull_fraction'],
         'mean_abs_decoder_delta':report['mean_abs_decoder_delta'],
         'bank_interior_class_accuracy':report['bank_interior_class_accuracy'],
         'bank_dev':report['per_bank'],
         'gate_percent':gate_percent,'small_threshold':small_threshold}
    if train is not None:
        training=_split_probe(system,train,gate_percent,small_threshold)
        out['train']=training['joint']
        out['bank_train']=training['per_bank']
        out['generalization_gap']={
            'overall_accuracy_train_minus_dev':training['joint']['accuracy_5pct']-report['joint']['accuracy_5pct'],
            'small_accuracy_train_minus_dev':training['joint']['small_accuracy_5pct']-report['joint']['small_accuracy_5pct'],
            'small_mae_dev_minus_train':report['joint']['small_mae']-training['joint']['small_mae']}
    return out


def rank(report,kind):
    j=report['joint']
    if kind=='small':return (j['small_accuracy_5pct'],j['accuracy_5pct'])
    if kind=='macro':return (j['accuracy_5pct'],j['small_accuracy_5pct'])
    if kind=='all_nine':return (min(j['per_tier'][t]['accuracy_5pct'] for t in TIERS),j['accuracy_5pct'])
    raise ValueError('JOINT_UNKNOWN_RANK')


def validate(args):
    floats=(args.relative_max,args.small_boost,args.small_threshold,args.lr,
            args.gaussian_weight,args.head_weight,args.numeric_weight,args.auxiliary_gradient_ratio)
    if (args.additional_steps<0 or args.max_steps<1 or args.chunk_steps<1 or args.views<9 or
        args.hidden<8 or args.train_per_tier<8 or args.dev_per_tier<8 or
        args.test_per_tier<8 or args.relative_max<=0 or args.small_boost<=0 or
        args.small_threshold<=0 or args.lr<=0 or
        not all(math.isfinite(x) for x in floats) or
        min(args.gaussian_weight,args.head_weight,args.numeric_weight,args.auxiliary_gradient_ratio)<0 or
        args.head_weight+args.numeric_weight<=0 or
        not 0<args.gate_percent<=100):
        raise ValueError('JOINT_INVALID_ARGUMENTS')
    output=args.output.expanduser().resolve()
    protected=[args.refinement_checkpoint.parent,args.refinement_checkpoint.parent.parent,
               args.source,args.teacher]
    if args.bank_init_checkpoint is not None:
        protected += [args.bank_init_checkpoint.parent]
    if args.decoder_init_checkpoint is not None:
        protected += [args.decoder_init_checkpoint.parent]
    fine.fault.verify_no_overlap(output,protected)
    return output


def verify_bank_init(args,stage2_sha,original_sha,donor_sha):
    if args.bank_init_checkpoint is None:return None,None
    raw=master.checked_file(args.bank_init_checkpoint)
    ck=master.load_state(raw)
    ident=ck.get('identity',{})
    if (ck.get('schema') not in (prior.SCHEMA,LEGACY_JOINT_SCHEMA) or
        ident.get('stage2_sha256')!=stage2_sha or
        ident.get('original_parent_sha256')!=original_sha or
        ident.get('donor_sha256')!=donor_sha or
        ident.get('source_sha256')!=fork.fingerprint(args.source) or
        ident.get('teacher_sha256')!=fork.fingerprint(args.teacher) or
        ident.get('bank_count')!=16 or
        float(ident.get('relative_max',-1))!=args.relative_max or
        not isinstance(ck.get('bank_states'),dict) or
        set(ck['bank_states'])!={str(k) for k in STAGES}):
        raise ValueError('JOINT_BANK_INIT_IDENTITY_MISMATCH')
    return ck,sha(raw)


def verify_decoder_init(args,stage2_sha,original_sha,donor_sha,bank_sha):
    if args.decoder_init_checkpoint is None:return None,None
    raw=master.checked_file(args.decoder_init_checkpoint)
    ck=master.load_state(raw);ident=ck.get('identity',{})
    if (ck.get('schema')!=v2.SCHEMA or ident.get('stage2_sha256')!=stage2_sha or
        ident.get('original_parent_sha256')!=original_sha or
        ident.get('donor_sha256')!=donor_sha or
        ident.get('source_sha256')!=fork.fingerprint(args.source) or
        ident.get('teacher_sha256')!=fork.fingerprint(args.teacher) or
        ident.get('bank_count')!=16 or ident.get('relative_max')!=args.relative_max or
        ident.get('hidden')!=args.hidden or
        (bank_sha is not None and ident.get('bank_init_sha256')!=bank_sha) or
        not isinstance(ck.get('bank_states'),dict) or
        set(ck['bank_states'])!={str(k) for k in STAGES} or
        not isinstance(ck.get('head_state'),dict)):
        raise ValueError('JOINT_DECODER_INIT_IDENTITY_MISMATCH')
    return ck,sha(raw)


def identity(args,first,original,donor,first_meta,bank_sha,bank_schema=None):
    return {'schema':SCHEMA,'stage2_sha256':sha(first),
            'original_parent_sha256':sha(original),'donor_sha256':sha(donor),
            'source_sha256':first_meta['source_sha256'],
            'teacher_sha256':first_meta['teacher_sha256'],
            'stage2_step':int(first_meta['step']),
            'original_bridge_arm':first_meta['parent_arm'],
            'refinement_checkpoint_path':str(args.refinement_checkpoint.expanduser().resolve()),
            'bank_init_sha256':bank_sha,'bank_init_schema':bank_schema,
            'decoder_init_sha256':getattr(args,'_decoder_init_sha256',None),
            'decoder_init_path':str(args.decoder_init_checkpoint.expanduser().resolve()) if args.decoder_init_checkpoint else None,
            'bank_init_path':str(args.bank_init_checkpoint.expanduser().resolve()) if args.bank_init_checkpoint else None,
            'relative_max':args.relative_max,'bank_count':16,
            'geometry':'fixed_zero_centered_halving_with_exact_overflow_buckets',
            'head':'direct_joint_signed_asinh_decoder_from_all_16_bank_distributions',
            'training':'numerical_first_gradient_bounded_gaussian_auxiliary_v3',
            'sampler':'half_original_tier_half_present_log_magnitude_bands',
            'max_steps':args.max_steps,'chunk_steps':args.chunk_steps,
            'views':args.views,'hidden':args.hidden,
            'train_per_tier':args.train_per_tier,'dev_per_tier':args.dev_per_tier,
            'test_per_tier':args.test_per_tier,'seed':args.seed,
            'small_threshold':args.small_threshold,'small_boost':args.small_boost,
            'lr':args.lr,'gaussian_weight':args.gaussian_weight,
            'head_weight':args.head_weight,'numeric_weight':args.numeric_weight,
            'gate_percent':args.gate_percent,'auxiliary_gradient_ratio':args.auxiliary_gradient_ratio,
            'benchmark_visibility':'upstream 011 exposes Y; no target values in forward inference'}


def save_state(system,opt,ident,step,args):
    return {'schema':SCHEMA,'identity':ident,'step':step,
            'bank_states':{str(k):system.cascade.banks[str(k)].state_dict() for k in STAGES},
            'head_state':system.head.state_dict(),'optimizer':opt.state_dict(),
            'rng_cpu':torch.get_rng_state(),
            'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}


def save_best(out,state,report):
    result=[]
    for metric in ('small','macro','all_nine'):
        if metric=='all_nine' and not report['joint']['all_nine_pass']:continue
        dest=out/'best_checkpoints'/f'best_{metric}.pt';meta=dest.with_suffix('.json')
        current=rank(report,metric)
        if dest.exists()!=meta.exists():raise ValueError('JOINT_PARTIAL_BEST')
        if meta.exists():
            old=json.loads(meta.read_text(encoding='utf8'))
            if (old['checkpoint_sha256']!=sha(master.checked_file(dest)) or
                old['stage2_sha256']!=state['identity']['stage2_sha256'] or
                old['bank_init_sha256']!=state['identity']['bank_init_sha256']):
                raise ValueError('JOINT_BEST_INTEGRITY_DRIFT')
            if current<=tuple(old['rank']):continue
        buf=io.BytesIO();torch.save(state,buf);raw=buf.getvalue()
        store.atomic_bytes(dest,raw)
        store.atom_json(meta,{'schema':SCHEMA+'.best.v1','metric':metric,
                             'step':state['step'],'rank':current,
                             'stage2_sha256':state['identity']['stage2_sha256'],
                             'bank_init_sha256':state['identity']['bank_init_sha256'],
                             'checkpoint_sha256':sha(raw)})
        result.append({'metric':metric,'step':state['step'],
                       'macro':report['joint']['accuracy_5pct'],
                       'small':report['joint']['small_accuracy_5pct']})
    return result


def construct_system(feature_width,first,device,args):
    first_ck=master.load_state(first)
    cfg=first_ck['identity']
    stage2=fine.LocalRefiner(feature_width,kind='gaussian',registers=int(cfg['registers']),
             sigma_bins=float(cfg['sigma_bins']),floor=float(cfg['floor']),
             fraction=float(cfg['fraction']),temperature=float(cfg['temperature'])).to(device)
    stage2.load_state_dict(first_ck['heads']['gaussian'],strict=True)
    system=JointGaussianSystem(feature_width,stage2,relative_max=args.relative_max,
                               hidden=args.hidden).to(device)
    return system


def run(args):
    out=validate(args)
    names=('stage2_snapshot.pt','parent_snapshot.pt','donor_snapshot.pt','config.json','latest.pt')
    exist=[(out/name).exists() for name in names]
    if any(exist) and not all(exist):raise ValueError('JOINT_PARTIAL_RUN_STATE')
    resume=all(exist)
    if resume:
        first,original,donor=[master.checked_file(out/name) for name in names[:3]]
        first_id=master.load_state(first)['identity']
        first_meta={**first_id,'step':master.load_state(first)['step']}
        live_first,live_original,live_donor,_=prior.check_parent(args)
        if (sha(live_first)!=sha(first) or sha(live_original)!=sha(original) or
            sha(live_donor)!=sha(donor)):
            raise ValueError('JOINT_SOURCE_SNAPSHOT_DRIFT')
        initial,bank_sha=verify_bank_init(args,sha(first),sha(original),sha(donor))
        decoder_initial,decoder_sha=verify_decoder_init(args,sha(first),sha(original),sha(donor),bank_sha)
        args._decoder_init_sha256=decoder_sha
        ident=identity(args,first,original,donor,first_meta,bank_sha,initial['schema'] if initial else None)
        previous=json.loads((out/'config.json').read_text(encoding='utf8'))
        if ident!=previous:raise ValueError('JOINT_RESUME_IDENTITY_DRIFT')
        saved=master.load_state(master.checked_file(out/'latest.pt'))
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('JOINT_BAD_RESUME_STATE')
        start=int(saved['step'])
    else:
        first,original,donor,metadata=prior.check_parent(args)
        first_id=master.load_state(first)['identity']
        first_meta={**first_id,'step':master.load_state(first)['step']}
        initial,bank_sha=verify_bank_init(args,sha(first),sha(original),sha(donor))
        decoder_initial,decoder_sha=verify_decoder_init(args,sha(first),sha(original),sha(donor),bank_sha)
        args._decoder_init_sha256=decoder_sha
        ident=identity(args,first,original,donor,first_meta,bank_sha,initial['schema'] if initial else None)
        saved=None;start=0
    if not args.test_only and start+args.additional_steps>args.max_steps:
        raise ValueError(f'JOINT_BUDGET_EXCEEDED:start={start},requested={args.additional_steps},max={args.max_steps}')
    fork.emit('joint_prepare',step=start,max_steps=args.max_steps,bank_count=16,
              decoder_init_sha256=args._decoder_init_sha256,
              stage2_sha256=ident['stage2_sha256'],bank_init_sha256=ident['bank_init_sha256'],
              test_split_accessed=args.test_only)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    orig_ck=master.load_state(original);donor_ck=master.load_state(donor)
    original_args=argparse.Namespace(arm=ident['original_bridge_arm'])
    decoder,bridge=fine.init_frozen(original_args,orig_ck,donor_ck,model)
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',{
        'schema':SCHEMA+'.capture','source_sha256':ident['source_sha256'],
        'teacher_sha256':ident['teacher_sha256'],
        'max_prompt_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    if args.test_only:
        if saved is None:raise ValueError('JOINT_TEST_NEEDS_RUN')
        path=out/'best_checkpoints'/f'best_{args.best_metric}.pt'
        raw=master.checked_file(path);metadata=json.loads(path.with_suffix('.json').read_text())
        if metadata['checkpoint_sha256']!=sha(raw) or metadata['metric']!=args.best_metric:
            raise ValueError('JOINT_TEST_BAD_BEST')
        chosen=master.load_state(raw)
        if chosen['identity']!=ident:raise ValueError('JOINT_TEST_IDENTITY_DRIFT')
        test,_=prior.load_features(args,model,decoder,bridge,cache,'test')
        system=construct_system(test[TIERS[0]]['features'].shape[-1],first,model['device'],args)
        for k in STAGES:system.cascade.banks[str(k)].load_state_dict(chosen['bank_states'][str(k)])
        system.head.load_state_dict(chosen['head_state'])
        result=probe(system,test,args.gate_percent,args.small_threshold,chosen['step'])
        result['schema']=SCHEMA+'.untouched-test.v1'
        result['best_metric']=args.best_metric
        result['checkpoint_sha256']=sha(raw)
        store.atom_json(out/f'test-{args.best_metric}-step-{chosen["step"]:07d}.json',result)
        fork.emit('joint_test_finished',step=chosen['step'],metric=args.best_metric,
                  accuracy=result['joint']['accuracy_5pct'],small=result['joint']['small_accuracy_5pct'])
        return result
    train,tr_meta=prior.load_features(args,model,decoder,bridge,cache,'train')
    dev,dev_meta=prior.load_features(args,model,decoder,bridge,cache,'dev')
    if {str(row['instance']) for t in TIERS for row in tr_meta[t]} & \
       {str(row['instance']) for t in TIERS for row in dev_meta[t]}:
        raise ValueError('JOINT_TRAIN_DEV_INSTANCE_LEAKAGE')
    sampler=MagnitudeBalancedSampler(train,args.relative_max)
    fork.emit('joint_support',magnitude_band_counts={str(g):len(v) for g,v in sampler.groups.items()},
              total=len(sampler.all_pairs))
    width=train[TIERS[0]]['features'].shape[-1]
    system=construct_system(width,first,model['device'],args)
    trainable=list(system.head.parameters())
    for k in STAGES:trainable += list(system.cascade.banks[str(k)].parameters())
    opt=torch.optim.AdamW(trainable,lr=args.lr)
    if saved is not None:
        for k in STAGES:system.cascade.banks[str(k)].load_state_dict(saved['bank_states'][str(k)])
        system.head.load_state_dict(saved['head_state'])
        opt.load_state_dict(saved['optimizer'])
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:
            torch.cuda.set_rng_state_all(saved['rng_cuda'])
    elif decoder_initial is not None:
        for k in STAGES:system.cascade.banks[str(k)].load_state_dict(decoder_initial['bank_states'][str(k)],strict=True)
        system.head.load_state_dict(decoder_initial['head_state'],strict=True)
    elif initial is not None:
        for k in STAGES:system.cascade.banks[str(k)].load_state_dict(initial['bank_states'][str(k)])
    if saved is None:
        out.mkdir(parents=True,exist_ok=True)
        store.atomic_bytes(out/'stage2_snapshot.pt',first)
        store.atomic_bytes(out/'parent_snapshot.pt',original)
        store.atomic_bytes(out/'donor_snapshot.pt',donor)
        store.atom_json(out/'config.json',ident)
    def save(step,loss_detail=None):
        state=save_state(system,opt,ident,step,args)
        store.atom_torch(out/'latest.pt',state)
        result=probe(system,dev,args.gate_percent,args.small_threshold,step,train=train)
        if loss_detail is not None:result['chunk_mean_train_losses_and_gradients']={k:float(v) for k,v in loss_detail.items()}
        store.atom_json(out/f'probe-step-{step:07d}.json',result)
        for row in save_best(out,state,result):fork.emit('joint_best_saved',**row)
        fork.emit('joint_probe',step=step,accuracy=result['joint']['accuracy_5pct'],
                  small=result['joint']['small_accuracy_5pct'],
                  base_accuracy=result['frozen_stage2']['accuracy_5pct'],
                  base_small=result['frozen_stage2']['small_accuracy_5pct'],
                  improved_vs_base_cases=result['improved_vs_base_cases'],
                  regressed_vs_base_cases=result['regressed_vs_base_cases'],
                  decoded_outside_candidate_hull_fraction=result['decoded_outside_candidate_hull_fraction'],
                  train_accuracy=result['train']['accuracy_5pct'],
                  train_small=result['train']['small_accuracy_5pct'],
                  train_dev_gap=result['generalization_gap'],
                  losses=result.get('chunk_mean_train_losses_and_gradients'))
        return result
    if saved is None:save(0)
    chunk_totals={};chunk_count=0
    for step in range(start+1,start+args.additional_steps+1):
        system.train();system.cascade.stage2.eval()
        batch=sampler.draw(args.seed,step,args.views)
        opt.zero_grad(set_to_none=True)
        primary,auxiliary,components=training_objective(system,batch,
            small_boost=args.small_boost,small_threshold=args.small_threshold,
            gaussian_weight=args.gaussian_weight,head_weight=args.head_weight,
            numeric_weight=args.numeric_weight,return_terms=True)
        if not bool(torch.isfinite(primary+auxiliary)):
            raise FloatingPointError('JOINT_NONFINITE_LOSS')
        gradients=numerical_first_backward(system,primary,auxiliary,
            auxiliary_gradient_ratio=args.auxiliary_gradient_ratio)
        total=primary+gradients['gaussian_gradient_multiplier']*auxiliary
        torch.nn.utils.clip_grad_norm_(trainable,1.,error_if_nonfinite=True)
        opt.step()
        sample={'loss':float(total.detach()),**{k:float(v) for k,v in components.items()},**gradients}
        for key,val in sample.items():chunk_totals[key]=chunk_totals.get(key,0.)+val
        chunk_count+=1
        if step%args.chunk_steps==0 or step==start+args.additional_steps:
            save(step,{k:v/chunk_count for k,v in chunk_totals.items()})
            chunk_totals={};chunk_count=0
    for name,key in [('stage2_snapshot.pt','stage2_sha256'),
                     ('parent_snapshot.pt','original_parent_sha256'),
                     ('donor_snapshot.pt','donor_sha256')]:
        if sha(master.checked_file(out/name))!=ident[key]:
            raise ValueError('JOINT_FROZEN_SNAPSHOT_DRIFT')
    fork.emit('joint_finished',step=start+args.additional_steps,
              latest=str(out/'latest.pt'),test_split_read=False)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refinement-checkpoint',type=Path,default=prior.DEFAULT_PARENT)
    p.add_argument('--bank-init-checkpoint',type=Path,default=None,
                   help='Optional old overflow-v2 or joint-head-v1 latest.pt: import only the 16 banks (read-only)')
    p.add_argument('--decoder-init-checkpoint',type=Path,default=None,
                   help='Optional completed v2 direct decoder latest.pt (read-only): import bank and head weights; reset optimizer for v3 loss')
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--additional-steps',type=int,default=300)
    p.add_argument('--max-steps',type=int,default=48000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--relative-max',type=float,default=RELATIVE_MAX)
    p.add_argument('--views',type=int,default=32)
    p.add_argument('--hidden',type=int,default=128)
    p.add_argument('--train-per-tier',type=int,default=256)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--test-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--gate-percent',type=float,default=40.)
    p.add_argument('--small-threshold',type=float,default=250000.)
    p.add_argument('--small-boost',type=float,default=2.)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--gaussian-weight',type=float,default=.10)
    p.add_argument('--head-weight',type=float,default=1.,
                   help='Weight of signed-asinh direct numerical decoder loss')
    p.add_argument('--numeric-weight',type=float,default=4.,
                   help='Weight of log(1 + absolute-relative-error) numerical loss')
    p.add_argument('--auxiliary-gradient-ratio',type=float,default=.25,
                   help='Maximum Gaussian auxiliary / primary numerical bank gradient norm ratio (0..1)')
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--test-only',action='store_true')
    p.add_argument('--best-metric',choices=('small','macro','all_nine'),default='small')
    return p


def main(argv=None):run(parser().parse_args(argv))

if __name__=='__main__':main()
