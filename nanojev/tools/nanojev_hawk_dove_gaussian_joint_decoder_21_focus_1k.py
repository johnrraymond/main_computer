#!/usr/bin/env python3
"""FOCUSED 1–1,000 learnability experiment with 21 Gaussian banks and one decoder.

Adds five finer banks to v3 without modifying the frozen Stage 2 model or
existing 16-bank runs. The legacy 16-bank decoder is preserved as one branch;
a new zero-initialized branch consumes ALL 21 probability distributions.
Together they produce one unrestricted signed-asinh numerical correction.
All 21 banks are independently supervised with exactly one-hot overflow
labels and receive end-to-end numerical gradients. No staged bank selection.

The old 16-bank v3 (or v2) checkpoint may be imported read-only: its 16 bank
states and original decoder branch are copied exactly; five new banks and the
21-bank decoder branch start new. This EXPERIMENT generates a new certified curriculum and preserves old evaluation.
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
from tools import nanojev_hawk_dove_gaussian_joint_decoder_16_v3 as old_v3
from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_correction_ab as store
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_internal_curriculum_master as master
from tools import nanojev_hawk_dove_nested90_expected as mixture
from tools import nanojev_hawk_dove_focus_1k_curriculum as focus
from tools import nanojev_hawk_dove_gaussian_joint_decoder_21 as old21

SCHEMA = 'nanojev.hawk-dove.gaussian-joint-decoder-21.focus-1k.learnability.v1'
STAGES = tuple(range(3, 24))
LEGACY_STAGES = prior.STAGES
BANK_COUNT = len(STAGES)
LEGACY_BANK_COUNT = len(LEGACY_STAGES)
TIERS = prior.TIERS
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_learnability_focus_1k_v1'
RELATIVE_MAX = prior.RELATIVE_MAX
LEGACY_JOINT_SCHEMA = 'nanojev.hawk-dove.gaussian-joint-head-16.v1'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixed_radius(stage, relative_max=RELATIVE_MAX):
    """All 21 banks halve about zero. Bank 21 spans +/-1.9073486328125 at 2M."""
    if (stage not in STAGES or not math.isfinite(relative_max) or relative_max<=0):
        raise ValueError('JOINT_INVALID_RADIUS')
    return float(relative_max) * 2.0 ** (-(stage - 3))


class ExpandedCascade(nn.Module):
    """Frozen Stage 2, 21 independent Gaussian banks, no sequential routing."""
    def __init__(self, base_width, stage2, *, relative_max=RELATIVE_MAX):
        super().__init__()
        if not math.isfinite(relative_max) or relative_max <= 0:
            raise ValueError('JOINT_BAD_RELATIVE_MAX')
        self.stage2 = stage2
        self.stage2.eval().requires_grad_(False)
        self.relative_max = float(relative_max)
        bank_width = base_width + stage2.registers + 6
        self.banks = nn.ModuleDict({str(k): prior.ZeroOverflowBank(
            bank_width, radius=fixed_radius(k, relative_max),
            registers=stage2.registers) for k in STAGES})

    def bank_input(self, data):
        with torch.no_grad():
            origin = data['coarse']
            base, info = self.stage2(data['features'], origin)
            features = prior.scalar_features(data['features'], origin, base, origin, info)
        return base, info, features


class JointNumericalHead(nn.Module):
    """Legacy 16-bank decoder + zero-initialized all-21-bank direct decoder.

    The original head is intentionally a separate branch: expanding the input
    of its LayerNorm would otherwise change its mean/variance and corrupt an
    imported v3 checkpoint even before training. At initialization, the new
    all-21 branch contributes *exactly zero*. Both branches are trainable.
    """
    def __init__(self, registers: int, *, count: int = BANK_COUNT, hidden: int = 128):
        super().__init__()
        if count != BANK_COUNT:
            raise ValueError('JOINT_BANK_COUNT_MISMATCH')
        self.legacy = old_v3.JointNumericalHead(registers, count=LEGACY_BANK_COUNT,
                                                hidden=hidden)
        self.full21 = old_v3.JointNumericalHead(registers, count=count,
                                                hidden=hidden)

    def forward(self, bank_probs, bank_estimates, radii, base, relative_max):
        if bank_probs.shape[1] != BANK_COUNT or bank_estimates.shape[1] != BANK_COUNT:
            raise ValueError('JOINT_BANK_OUTPUT_COUNT_MISMATCH')
        legacy_delta = self.legacy(bank_probs[:, :LEGACY_BANK_COUNT],
                                   bank_estimates[:, :LEGACY_BANK_COUNT],
                                   radii[:LEGACY_BANK_COUNT], base, relative_max)
        new_delta = self.full21(bank_probs, bank_estimates, radii, base, relative_max)
        return legacy_delta + new_delta


class JointGaussianSystem(nn.Module):
    def __init__(self, base_width, stage2, *, relative_max=RELATIVE_MAX, hidden=128):
        super().__init__()
        self.cascade = ExpandedCascade(base_width, stage2, relative_max=relative_max)
        self.head = JointNumericalHead(stage2.registers, hidden=hidden)
        self.relative_max = float(relative_max)
        self.register_buffer('radii', torch.tensor(
            [fixed_radius(k, relative_max) for k in STAGES], dtype=torch.float32))

    def forward(self, data):
        base, _, features = self.cascade.bank_input(data)
        per_bank = [self.cascade.banks[str(k)](features) for k in STAGES]
        bank_estimates = torch.stack([entry[0] for entry in per_bank], dim=1)
        bank_probs = torch.stack([entry[1]['probabilities'] for entry in per_bank], dim=1)
        encoded_base = torch.asinh(base.float())
        delta = self.head(bank_probs, bank_estimates, self.radii, base, self.relative_max)
        encoded_prediction = encoded_base + delta
        bounded_prediction = encoded_prediction.clamp(-20., 20.)
        predicted = base + (torch.sinh(bounded_prediction) - torch.sinh(encoded_base))
        return predicted, {'base': base, 'decoded_coordinate': encoded_prediction,
                           'decoder_delta': delta, 'bank_probs': bank_probs,
                           'bank_estimates': bank_estimates}


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
    bootstrap_used = numerical_bank_norm <= 1e-12 and numerical_head_norm > 1e-12 and auxiliary_norm > 1e-12
    if bootstrap_used:
        # A zero-init decoder makes initial d(primary)/d(bank) exactly zero.
        # A rule that caps auxiliary grad against that ZERO norm otherwise
        # immobilizes every bank. Permit at most 1% of head gradient norm for
        # this initialization case, then revert to usual numerical-bank cap.
        ratio_cap=min(1., .01*numerical_head_norm / auxiliary_norm)
    else:
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
            'gaussian_to_numeric_bank_gradient_ratio':ratio_cap*auxiliary_norm/numerical_bank_norm if numerical_bank_norm>1e-20 else 0.,
            'gaussian_bank_bootstrap_used':float(bootstrap_used),
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



def controlled_subset(rows, count, seed):
    """Deterministic tier-stratified fit set; leave all held-out rows untouched.

    Retains the nested-90 magnitude proportions to rounding precision, instead
    of taking the first N potentially biased rows. Never repeats a prompt.
    """
    if not count:
        return rows
    result = {}
    for tier in TIERS:
        source = rows[tier]
        if not 1 <= count <= len(source):
            raise ValueError('LEARNABILITY_SUBSET_OUT_OF_RANGE')
        groups = {k: [] for k in range(8)}
        for row in source:
            groups[mixture.bin_for(float(row['x']))].append(row)
        quotas = list(__import__('tools.nanojev_hawk_dove_nested90_mixture', fromlist=['quotas']).quotas(count))
        rng = random.Random(f'learnability_subset/{seed}/{tier}/{count}')
        picked = []
        for k,need in enumerate(quotas):
            if len(groups[k]) < need:
                raise ValueError('LEARNABILITY_MISSING_MAGNITUDE_SUPPORT')
            picked.extend(rng.sample(groups[k],need))
        rng.shuffle(picked)
        if len({str(row['instance']) for row in picked}) != count:
            raise ValueError('LEARNABILITY_SUBSET_DUPLICATE')
        result[tier] = picked
    return result


class RetentionSampler:
    """Blend genuine original integer examples with nested-90 fractional examples.

    Fractional nested-90 quotas are unchanged *within the fractional stream*.
    Integer replay occupies a separately disclosed slice of optimizer batches.
    Both sources are from TRAIN only; neither development nor TEST is sampled.
    """
    def __init__(self, fractional, integer, *, retention_fraction, relative_max):
        if not 0. < retention_fraction < 1.:
            raise ValueError('JOINT_INVALID_RETENTION_FRACTION')
        self.fractional=mixture.Sampler(fractional)
        self.integer=MagnitudeBalancedSampler(integer,relative_max)
        self.fraction=retention_fraction

    def counts(self, views):
        if views < 4:
            raise ValueError('JOINT_RETENTION_VIEWS_TOO_SMALL')
        integer_count=max(2,min(views-2,round(views*self.fraction)))
        return views-integer_count,integer_count

    def draw(self, seed, step, views):
        fractional_count,integer_count=self.counts(views)
        a=self.fractional.draw(seed,step,fractional_count)
        b=self.integer.draw(seed+17011,step,integer_count)
        # Source-isolated selection first; no synthetic labels and no crossover.
        result={k:torch.cat((a[k],b[k]),dim=0) for k in ('features','coarse','target')}
        # Shuffle only row order for reproducible stream mixing.
        rng=random.Random(f'retention/{seed}/{step}/{views}')
        order=list(range(views));rng.shuffle(order)
        index=torch.tensor(order,device=result['target'].device)
        return {k:v.index_select(0,index) for k,v in result.items()}


def frozen_feature_resolution(split):
    """Detect exact frozen-feature aliases among distinct subunit oracle targets.

    Per tier, these features are what the Gaussian model actually receives,
    *after* Qwen tokenization and the original decoder capture. Exact aliases
    mean the downstream bank/head cannot distinguish those training examples.
    """
    result={}
    for tier in TIERS:
        group=split[tier];y=group['target'].detach().cpu().float()
        feats=group['features'].detach().cpu().float()
        chosen=(y.abs()<1).nonzero(as_tuple=False).reshape(-1).tolist()
        collisions={};downstream={}
        coarse=group['coarse'].detach().cpu().float()
        for i in chosen:
            v=feats[i].contiguous().numpy().tobytes()
            digest=hashlib.sha256(v).hexdigest()
            collisions.setdefault(digest,[]).append(float(y[i]))
            # The downstream stage2 and 21 banks read BOTH frozen features
            # and the coarse estimate. A shared feature alone is not proof
            # of indistinguishable complete inputs.
            full=hashlib.sha256(v+coarse[i].contiguous().numpy().tobytes()).hexdigest()
            downstream.setdefault(full,[]).append(float(y[i]))
        alias=[v for v in collisions.values() if len(set(v))>1]
        complete_alias=[v for v in downstream.values() if len(set(v))>1]
        result[tier]={'subunit_cases':len(chosen),'unique_frozen_vectors':len(collisions),
                      'aliased_distinct_target_cases':sum(len(v) for v in alias),
                      'max_aliased_target_span':max((max(v)-min(v) for v in alias),default=0.),
                      'unique_downstream_inputs':len(downstream),
                      'aliased_downstream_distinct_target_cases':sum(len(v) for v in complete_alias),
                      'max_downstream_aliased_target_span':max((max(v)-min(v) for v in complete_alias),default=0.)}
    return result


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



def magnitude_band_metrics(records):
    """Measure actual performance *per nested90 exclusive answer band*.

    Include both the existing /max(1,abs(X)) metric and strict relative
    accuracy for subunit nonzero X, because they differ substantially.
    These are held-out probe computations only; never read the TEST split.
    """
    import collections
    grouped=collections.defaultdict(lambda: {'n':0,'correct':0,'strict_nonzero_n':0,
             'strict_correct':0,'base_correct':0,'mae_sum':0.,'asinh_abs_sum':0.,'frozen_asinh_sum':0.,'zero_asinh_sum':0.,'zero_correct':0,'zero_mae_sum':0.,'fixed':0,'broken':0})
    for y,pred,base in records:
        for truth,value,frozen in zip(y.detach().cpu().tolist(),pred.detach().cpu().tolist(),base.detach().cpu().tolist()):
            band=mixture.bin_for(truth)
            a=grouped[band];a['n']+=1
            delta=abs(value-truth);base_delta=abs(frozen-truth)
            ok=delta/max(1.,abs(truth))<=.05
            old=base_delta/max(1.,abs(truth))<=.05
            a['correct']+=int(ok);a['base_correct']+=int(old)
            a['mae_sum']+=delta
            a['asinh_abs_sum']+=abs(math.asinh(value)-math.asinh(truth))
            a['frozen_asinh_sum']+=abs(math.asinh(frozen)-math.asinh(truth))
            a['zero_asinh_sum']+=abs(math.asinh(truth))
            a['zero_correct']+=int(abs(truth)/max(1.,abs(truth))<=.05)
            a['zero_mae_sum']+=abs(truth)
            a['fixed']+=int(ok and not old);a['broken']+=int(old and not ok)
            if truth!=0:
                a['strict_nonzero_n']+=1
                a['strict_correct']+=int(delta/abs(truth)<=.05)
    result={}
    for band in range(8):
        a=grouped[band];n=a['n'];nonzero=a['strict_nonzero_n']
        result[str(band)]={'n':n,'accuracy_5pct_floor':a['correct']/n if n else None,
              'frozen_accuracy_5pct_floor':a['base_correct']/n if n else None,
              'strict_relative_accuracy_5pct_nonzero':a['strict_correct']/nonzero if nonzero else None,
              'nonzero_n':nonzero,'mae':a['mae_sum']/n if n else None,
              'mean_absolute_asinh_error':a['asinh_abs_sum']/n if n else None,
              'frozen_mean_absolute_asinh_error':a['frozen_asinh_sum']/n if n else None,
              'zero_predictor_mean_absolute_asinh_error':a['zero_asinh_sum']/n if n else None,
              'zero_predictor_accuracy_5pct_floor':a['zero_correct']/n if n else None,
              'zero_predictor_mae':a['zero_mae_sum']/n if n else None,
              'fixed_vs_stage2':a['fixed'],'broken_vs_stage2':a['broken']}
    return result


@torch.no_grad()
def _split_probe(system,split,gate_percent,small_threshold,*,compare_base=False):
    by_tier={};base_by_tier={};records=[];magnitude_records=[];by_tier_magnitude={}
    improved=regressed=outside_hull=0;delta_abs_sum=0.;count=0
    for tier in TIERS:
        data=split[tier];pred,detail=system(data)
        records.append((data,detail))
        y=data['target'];magnitude_records.append((y,pred,detail['base']))
        by_tier_magnitude[tier]=magnitude_band_metrics([(y,pred,detail['base'])])['7']
        by_tier[tier]=metrics(y,pred,small_threshold)
        if compare_base:
            base_by_tier[tier]=metrics(y,detail['base'],small_threshold)
            before=((detail['base']-y).abs()/y.abs().clamp_min(1.))<=.05
            after=((pred-y).abs()/y.abs().clamp_min(1.))<=.05
            improved+=int((after & ~before).sum());regressed+=int((before & ~after).sum())
            bounds=torch.cat((detail['base'][:,None],detail['bank_estimates']),dim=1)
            outside_hull+=int(((pred<bounds.min(-1).values-1e-4)|
                               (pred>bounds.max(-1).values+1e-4)).sum())
            count+=len(y);delta_abs_sum+=float(detail['decoder_delta'].abs().sum())
    abs_err=[];asinh_err=[];zero_asinh_err=[];frozen_asinh_err=[];per_tier_asinh={}
    for tier in TIERS:
        data=split[tier]
        # The records align exactly with TIERS iteration order.
        i=TIERS.index(tier);y=data['target'];pred=magnitude_records[i][1]
        abs_err.append((pred-y).abs().reshape(-1))
        asinh_err.append((torch.asinh(pred)-torch.asinh(y)).abs().reshape(-1))
        zero_asinh_err.append(torch.asinh(y).abs().reshape(-1))
        frozen_asinh_err.append((torch.asinh(magnitude_records[i][2])-torch.asinh(y)).abs().reshape(-1))
        per_tier_asinh[tier]=float(asinh_err[-1].mean())
    per_tier_magnitude={tier:magnitude_band_metrics([magnitude_records[i]])
                        for i,tier in enumerate(TIERS)}
    result={'mae':float(torch.cat(abs_err).mean()),
            'mean_absolute_asinh_error':float(torch.cat(asinh_err).mean()),
            'zero_predictor_mean_absolute_asinh_error':float(torch.cat(zero_asinh_err).mean()),
            'frozen_mean_absolute_asinh_error':float(torch.cat(frozen_asinh_err).mean()),
            'per_tier_absolute_asinh_error':per_tier_asinh,
            'joint':_aggregate_metrics(by_tier,gate_percent),
            'per_bank':_per_bank_stats(system,records),
            'magnitude_bands':magnitude_band_metrics(magnitude_records),
            'per_tier_magnitude_bands':per_tier_magnitude,
            'below_one_by_tier':by_tier_magnitude}
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
         'magnitude_bands_original_dev':report['magnitude_bands'],
         'gate_percent':gate_percent,'small_threshold':small_threshold}
    if train is not None:
        training=_split_probe(system,train,gate_percent,small_threshold)
        out['train']=training['joint']
        out['bank_train']=training['per_bank']
        out['magnitude_bands_train']=training['magnitude_bands']
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
            args.gaussian_weight,args.head_weight,args.numeric_weight,args.auxiliary_gradient_ratio,
            args.retention_fraction,args.max_original_dev_drop,args.max_original_dev_collapse,
            args.required_new_dev_gain)
    if (args.additional_steps<0 or args.max_steps<1 or args.chunk_steps<1 or args.views<9 or
        args.retention_fraction != 0 or args.learnability_train_per_tier != 0 or
        args.hidden<8 or args.train_per_tier<8 or args.dev_per_tier<8 or
        args.test_per_tier<8 or args.mixture_train_per_tier<8 or
        args.mixture_new_dev_per_tier<1 or args.relative_max<=0 or args.small_boost<=0 or
        not 0. <= args.retention_fraction < 1. or args.views<4 or
        args.learnability_train_per_tier<0 or
        (args.learnability_train_per_tier and args.learnability_train_per_tier>args.mixture_train_per_tier) or
        not 0. <= args.max_original_dev_drop <= args.max_original_dev_collapse <= 1. or
        args.required_new_dev_gain<0 or
        args.small_threshold<=0 or args.lr<=0 or
        not all(math.isfinite(x) for x in floats) or
        min(args.gaussian_weight,args.head_weight,args.numeric_weight,args.auxiliary_gradient_ratio)<0 or
        args.head_weight+args.numeric_weight<=0 or
        not 0<args.gate_percent<=100):
        raise ValueError('JOINT_INVALID_ARGUMENTS')
    output=args.output.expanduser().resolve()
    protected=[args.refinement_checkpoint.parent,args.refinement_checkpoint.parent.parent,
               args.source,args.teacher,args.mixture_fixture]
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
        ident.get('bank_count')!=LEGACY_BANK_COUNT or
        float(ident.get('relative_max',-1))!=args.relative_max or
        not isinstance(ck.get('bank_states'),dict) or
        set(ck['bank_states'])!={str(k) for k in LEGACY_STAGES}):
        raise ValueError('JOINT_BANK_INIT_IDENTITY_MISMATCH')
    return ck,sha(raw)


def verify_decoder_init(args,stage2_sha,original_sha,donor_sha,bank_sha):
    if args.decoder_init_checkpoint is None:return None,None
    raw=master.checked_file(args.decoder_init_checkpoint)
    ck=master.load_state(raw);ident=ck.get('identity',{})
    if (ck.get('schema') not in (v2.SCHEMA, old_v3.SCHEMA, old21.SCHEMA) or ident.get('stage2_sha256')!=stage2_sha or
        ident.get('original_parent_sha256')!=original_sha or
        ident.get('donor_sha256')!=donor_sha or
        ident.get('source_sha256')!=fork.fingerprint(args.source) or
        ident.get('teacher_sha256')!=fork.fingerprint(args.teacher) or
        ident.get('bank_count')!=(BANK_COUNT if ck.get('schema')==old21.SCHEMA else LEGACY_BANK_COUNT) or ident.get('relative_max')!=args.relative_max or
        ident.get('hidden')!=args.hidden or
        (bank_sha is not None and ident.get('bank_init_sha256')!=bank_sha) or
        not isinstance(ck.get('bank_states'),dict) or
        set(ck['bank_states'])!={str(k) for k in (STAGES if ck.get('schema')==old21.SCHEMA else LEGACY_STAGES)} or
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
            'relative_max':args.relative_max,'bank_count':BANK_COUNT,
            'legacy_bank_count':LEGACY_BANK_COUNT,
            'geometry':'fixed_zero_centered_halving_with_exact_overflow_buckets',
            'head':'trainable_legacy16_decoder_plus_zero_initialized_all21_direct_decoder',
            'training':'focused_1_to_1000_full_dataset_learnability',
            'experiment_arm':args.experiment_arm,
            'learnability_train_per_tier':args.learnability_train_per_tier,
            'retention_fraction':args.retention_fraction,
            'max_original_dev_drop':args.max_original_dev_drop,
            'max_original_dev_collapse':args.max_original_dev_collapse,
            'required_new_dev_gain':args.required_new_dev_gain,
             'sampler':'focus_1k_three_band_balanced_no_downsample',
            'mixture_schema':focus.SCHEMA,
            'mixture_fixture_path':str(args.mixture_fixture.expanduser().resolve()),
            'mixture_fixture_sha256':args._mixture_fixture_sha256,
            'mixture_train_per_tier':args.mixture_train_per_tier,
            'mixture_new_dev_per_tier':args.mixture_new_dev_per_tier,
            'mixture_strict_unique':args.mixture_strict_unique,
            'mixture_reallocate_empty_bands':args.mixture_reallocate_empty_bands,
            'mixture_plan_sha256':args._mixture_plan_sha256,
            'mixture_generated_train_rows_sha256':args._mixture_train_sha256,
            'mixture_generated_dev_rows_sha256':args._mixture_dev_sha256,
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


def promotion_gate(report, *, max_original_dev_drop, required_new_dev_gain):
    """Both domains must pass for any best checkpoint to be promoted.

    Does not alter the current checkpoint; gate applies to best_* artifacts.
    Refuse missing or non-finite measurements rather than treating them as 0.
    """
    old=report.get('joint',{}).get('accuracy_5pct')
    old_base=report.get('frozen_stage2',{}).get('accuracy_5pct')
    old_small=report.get('joint',{}).get('small_accuracy_5pct')
    old_small_base=report.get('frozen_stage2',{}).get('small_accuracy_5pct')
    new=report.get('nested90_unseen_dev',{})
    new_head=new.get('joint',{}).get('accuracy_5pct')
    new_base=new.get('frozen_stage2',{}).get('accuracy_5pct')
    numbers=(old,old_base,old_small,old_small_base,new_head,new_base)
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in numbers):
        return {'promotable':False,'reason':'MISSING_OR_NONFINITE_DUAL_DOMAIN_EVIDENCE'}
    retention=old>=old_base-max_original_dev_drop
    small_retention=old_small>=old_small_base-max_original_dev_drop
    gain=new_head>new_base+required_new_dev_gain
    return {'promotable':bool(retention and small_retention and gain),
            'reason':'PASS' if retention and small_retention and gain else
                'ORIGINAL_RETENTION_FAILED' if not retention else
                'ORIGINAL_SMALL_RETENTION_FAILED' if not small_retention else 'NEW_DEV_GAIN_FAILED',
            'original_accuracy':old,'original_baseline':old_base,
            'original_allowed_drop':max_original_dev_drop,
            'original_small_accuracy':old_small,'original_small_baseline':old_small_base,
            'new_dev_accuracy':new_head,'new_dev_baseline':new_base,
            'new_dev_required_gain':required_new_dev_gain}


def save_best(out,state,report):
    gate=report.get('promotion_gate')
    if not isinstance(gate,dict) or not gate.get('promotable',False):
        return []
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




def build_mixture(args):
    """Sample original-oracle-certified EXPECTED count prompts in exactly three bands.

    Reject empty, duplicate and weakly covered distributions before any Qwen load.
    Original integer dev is used only for exclusion/measurement, never gradients.
    """
    payoffs, fixture_meta = mixture.certified_payoffs(args.mixture_fixture,args.teacher)
    excluded = mixture.original_counts(args.teacher,splits=('dev',))
    report, train_counts, dev_counts = focus.plan(
        payoffs, excluded, args.mixture_train_per_tier,
        args.mixture_new_dev_per_tier, args.seed)
    if not report['feasible']:
        raise ValueError('FOCUS_1K_UNREACHABLE_ORACLE_BANDS:'+json.dumps(report['unreachable_bands']))
    train_rows = mixture.render_rows(train_counts,payoffs,args.seed,'train')
    dev_rows = mixture.render_rows(dev_counts,payoffs,args.seed,'dev')
    train_ids={str(row['instance']) for row in train_rows[TIERS[0]]}
    dev_ids={str(row['instance']) for row in dev_rows[TIERS[0]]}
    if train_ids & dev_ids or len(train_ids)!=len(train_counts) or len(dev_ids)!=len(dev_counts):
        raise ValueError('FOCUS_1K_TRAIN_NEW_DEV_LEAKAGE')
    for name, _, _ in focus.BANDS:
        for group, expected_count in ((train_rows, report['requested_train_by_band'][name]),
                                      (dev_rows, report['requested_new_dev_by_band'][name])):
            for tier in TIERS:
                if sum(focus.band(row['x'])==name for row in group[tier]) != expected_count:
                    raise ValueError('FOCUS_1K_TIER_BAND_QUOTA_DRIFT')
    report['generated_train_prompt_count']=sum(map(len,train_rows.values()))
    report['generated_new_dev_prompt_count']=sum(map(len,dev_rows.values()))
    report['minimum_1_1000_examples_per_tier']=min(report['requested_train_by_band'].values())
    args._mixture_fixture_sha256=fixture_meta['sha256']
    args._mixture_train_sha256=mixture.source_signature(train_rows)
    args._mixture_dev_sha256=mixture.source_signature(dev_rows)
    args._mixture_plan_sha256=sha(json.dumps(report,sort_keys=True).encode('utf8'))
    return report,train_rows,dev_rows


def run(args):
    out=validate(args)
    mixture_report,mixture_train_rows,mixture_dev_rows=build_mixture(args)
    # NO implicit 32-example downsampling. All certified focused examples train.
    if args.learnability_train_per_tier != 0:
        raise ValueError('FOCUS_1K_FORBIDS_DOWNSAMPLING')
    mixture_report['learnability_train_per_tier']=len(mixture_train_rows[TIERS[0]])
    mixture_report['learnability_train_prompt_count']=sum(len(group) for group in mixture_train_rows.values())
    mixture_report['learnability_experiment_arm']=args.experiment_arm
    mixture_report['learnability_subset_sha256']=mixture.source_signature(mixture_train_rows)
    # Identity captures the *effective* prompts instead of only the generated pool.
    args._mixture_train_sha256=mixture_report['learnability_subset_sha256']
    args._mixture_plan_sha256=sha(json.dumps(mixture_report,sort_keys=True).encode('utf8'))
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
    fork.emit('joint_prepare',step=start,max_steps=args.max_steps,bank_count=BANK_COUNT,
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
    train=mixture.load_features(model,decoder,bridge,cache,args,mixture_train_rows,'train')
    dev,dev_meta=prior.load_features(args,model,decoder,bridge,cache,'dev')
    new_dev=mixture.load_features(model,decoder,bridge,cache,args,mixture_dev_rows,'dev') if mixture_dev_rows else None
    mixture_report['frozen_feature_resolution']={'train':frozen_feature_resolution(train),
                                                    'new_dev':frozen_feature_resolution(new_dev) if new_dev is not None else None}
    if {str(row['instance']) for t in TIERS for row in mixture_train_rows[t]} & \
       {str(row['instance']) for t in TIERS for row in dev_meta[t]}:
        raise ValueError('NESTED90_TRAIN_ORIGINAL_DEV_LEAKAGE')
    # Read original TRAIN only. Preserve the initial integer capability during
    # optimization; dev and TEST are never used as gradient-bearing examples.
    integer_train,integer_train_meta=prior.load_features(args,model,decoder,bridge,cache,'train')
    if {str(row['instance']) for t in TIERS for row in integer_train_meta[t]} & \
       {str(row['instance']) for t in TIERS for row in dev_meta[t]}:
        raise ValueError('JOINT_INTEGER_TRAIN_DEV_LEAKAGE')
    if args.retention_fraction:
        sampler=RetentionSampler(train,integer_train,retention_fraction=args.retention_fraction,
                                 relative_max=args.relative_max)
        frac_views,int_views=sampler.counts(args.views)
    else:
        sampler=focus.Sampler(train)
        frac_views,int_views=args.views,0
    mixture_report['optimizer_streams']={
        'fractional_examples_per_batch':frac_views,
        'original_integer_replay_per_batch':int_views,
        'fractional_ratio_actual':frac_views/args.views,
        'original_integer_replay_ratio_actual':int_views/args.views,
        'focus_1k_quotas_apply_to':'all_optimizer_examples',
        'integer_source':'original teacher TRAIN only',
    }
    fork.emit('joint_support',mixture=mixture_report,
              total=sum(len(train[t]['target']) for t in TIERS),
              integer_replay_total=sum(len(integer_train[t]['target']) for t in TIERS))
    width=train[TIERS[0]]['features'].shape[-1]
    system=construct_system(width,first,model['device'],args)
    if args.experiment_arm=='decoder-only':
        for k in STAGES:system.cascade.banks[str(k)].requires_grad_(False)
    trainable=list(system.head.parameters())
    if args.experiment_arm=='joint':
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
        source_stages = STAGES if decoder_initial['schema']==old21.SCHEMA else LEGACY_STAGES
        for k in source_stages:
            system.cascade.banks[str(k)].load_state_dict(decoder_initial['bank_states'][str(k)],strict=True)
        if decoder_initial['schema']==old21.SCHEMA:
            system.head.load_state_dict(decoder_initial['head_state'],strict=True)
        else:
            system.head.legacy.load_state_dict(decoder_initial['head_state'],strict=True)
    elif initial is not None:
        for k in LEGACY_STAGES:system.cascade.banks[str(k)].load_state_dict(initial['bank_states'][str(k)],strict=True)
    if saved is None:
        out.mkdir(parents=True,exist_ok=True)
        store.atomic_bytes(out/'stage2_snapshot.pt',first)
        store.atomic_bytes(out/'parent_snapshot.pt',original)
        store.atomic_bytes(out/'donor_snapshot.pt',donor)
        store.atom_json(out/'config.json',ident)
        store.atom_json(out/'nested90-feasibility.json',mixture_report)
    def save(step,loss_detail=None):
        state=save_state(system,opt,ident,step,args)
        store.atom_torch(out/'latest.pt',state)
        result=probe(system,dev,args.gate_percent,args.small_threshold,step,train=train)
        if new_dev is not None:
            result['nested90_unseen_dev']=_split_probe(system,new_dev,args.gate_percent,args.small_threshold,compare_base=True)
        train_probe=_split_probe(system,train,args.gate_percent,args.small_threshold)
        result['train_asinh_mae']=train_probe['mean_absolute_asinh_error']
        result['train_zero_asinh_mae']=train_probe['zero_predictor_mean_absolute_asinh_error']
        result['mixture_feasibility']=mixture_report
        result['focus_1k']={
            'train_by_band':{name:result['magnitude_bands_train'][str(idx)] for name,idx in (('1-10',6),('10-100',5),('100-1000',4))},
            'new_dev_by_band':{name:result['nested90_unseen_dev']['magnitude_bands'][str(idx)] for name,idx in (('1-10',6),('10-100',5),('100-1000',4))},
            'requested_train_by_band':mixture_report['requested_train_by_band'],
            'requested_new_dev_by_band':mixture_report['requested_new_dev_by_band'],
            'precision_goal':'five_percent_relative_error',
            'per_tier_new_dev':{tier:{name:values[str(index)] for name,index in (('1-10',6),('10-100',5),('100-1000',4))}
                                for tier,values in result['nested90_unseen_dev']['per_tier_magnitude_bands'].items()},
            'per_tier_train':{tier:{name:values[str(index)] for name,index in (('1-10',6),('10-100',5),('100-1000',4))}
                                for tier,values in train_probe['per_tier_magnitude_bands'].items()},
            'zero_baseline_is_trivial_in_all_three_bands':True}
        result['learnability']={'arm':args.experiment_arm,'train_per_tier':len(train[TIERS[0]]['target']),
            'fractional_train_mae_asinh':result['train_asinh_mae'],
            'fractional_train_zero_mae_asinh':result.get('train_zero_asinh_mae'),
            'fractional_new_dev_mae_asinh':result['nested90_unseen_dev']['mean_absolute_asinh_error'] if new_dev is not None else None,
            'fractional_new_dev_zero_mae_asinh':result['nested90_unseen_dev']['zero_predictor_mean_absolute_asinh_error'] if new_dev is not None else None,
            'fractional_new_dev_frozen_mae_asinh':result['nested90_unseen_dev']['frozen_mean_absolute_asinh_error'] if new_dev is not None else None,
            'new_dev_subunit_strict_5pct':result['nested90_unseen_dev']['magnitude_bands']['7']['strict_relative_accuracy_5pct_nonzero'] if new_dev is not None else None,
            'original_integer_accuracy':result['joint']['accuracy_5pct']}
        result['promotion_gate']=promotion_gate(result,
            max_original_dev_drop=args.max_original_dev_drop,
            required_new_dev_gain=args.required_new_dev_gain)
        result['integer_replay_train_accuracy']=_split_probe(
            system,integer_train,args.gate_percent,args.small_threshold)['joint']['accuracy_5pct']
        if loss_detail is not None:result['chunk_mean_train_losses_and_gradients']={k:float(v) for k,v in loss_detail.items()}
        store.atom_json(out/f'probe-step-{step:07d}.json',result)
        for row in save_best(out,state,result):fork.emit('joint_best_saved',**row)
        if new_dev is not None:
            # Research-only artifact: it is NEVER a deployment best and needs
            # no original accuracy gate. Objective is held-out asinh error.
            dest=out/'research_best_new_dev.pt'
            meta=out/'research_best_new_dev.json'
            score=result['learnability']['fractional_new_dev_mae_asinh']
            if not math.isfinite(score):
                raise FloatingPointError('LEARNABILITY_NONFINITE_DEV_METRIC')
            if dest.exists()!=meta.exists():
                raise ValueError('LEARNABILITY_PARTIAL_RESEARCH_CHECKPOINT')
            previous=json.loads(meta.read_text()) if meta.exists() else None
            if previous is not None and previous['checkpoint_sha256'] != sha(master.checked_file(dest)):
                raise ValueError('LEARNABILITY_RESEARCH_CHECKPOINT_INTEGRITY_DRIFT')
            if previous is None or score < previous['mean_absolute_asinh_error']:
                buf=io.BytesIO();torch.save(state,buf);raw=buf.getvalue()
                store.atomic_bytes(dest,raw)
                store.atom_json(meta,{'schema':SCHEMA+'.research-only.v1',
                    'step':step,'mean_absolute_asinh_error':score,
                    'checkpoint_sha256':sha(raw),'deployment_eligible':False})
                fork.emit('learnability_research_best_saved',step=step,
                          new_dev_mae_asinh=score)
        fork.emit('joint_promotion_gate',step=step,**result['promotion_gate'])
        fork.emit('focus_1k_probe',step=step,
                  train_by_band={k:{'n':v['n'],'accuracy_5pct':v['accuracy_5pct_floor'],'mae':v['mae']} for k,v in result['focus_1k']['train_by_band'].items()},
                  new_dev_by_band={k:{'n':v['n'],'accuracy_5pct':v['accuracy_5pct_floor'],'mae':v['mae']} for k,v in result['focus_1k']['new_dev_by_band'].items()},
                  chunk_losses=result.get('chunk_mean_train_losses_and_gradients'))
        fork.emit('joint_probe',step=step,accuracy=result['joint']['accuracy_5pct'],
                  small=result['joint']['small_accuracy_5pct'],
                  base_accuracy=result['frozen_stage2']['accuracy_5pct'],
                  base_small=result['frozen_stage2']['small_accuracy_5pct'],
                  improved_vs_base_cases=result['improved_vs_base_cases'],
                  regressed_vs_base_cases=result['regressed_vs_base_cases'],
                  decoded_outside_candidate_hull_fraction=result['decoded_outside_candidate_hull_fraction'],
                  train_accuracy=result['train']['accuracy_5pct'],
                  train_small=result['train']['small_accuracy_5pct'],
                  new_dev_accuracy=(result['nested90_unseen_dev']['joint']['accuracy_5pct'] if 'nested90_unseen_dev' in result else None),
                  new_dev_small=(result['nested90_unseen_dev']['joint']['small_accuracy_5pct'] if 'nested90_unseen_dev' in result else None),
                  new_dev_below_one=(result['nested90_unseen_dev']['magnitude_bands']['7']['accuracy_5pct_floor'] if 'nested90_unseen_dev' in result else None),
                  new_dev_below_one_strict=(result['nested90_unseen_dev']['magnitude_bands']['7']['strict_relative_accuracy_5pct_nonzero'] if 'nested90_unseen_dev' in result else None),
                  train_dev_gap=result['generalization_gap'],
                  losses=result.get('chunk_mean_train_losses_and_gradients'))
        if step>0 and (result['joint']['accuracy_5pct'] <
                       result['frozen_stage2']['accuracy_5pct']-args.max_original_dev_collapse):
            fork.emit('learnability_original_accuracy_drop_observed',step=step,
                      accuracy=result['joint']['accuracy_5pct'],
                      baseline=result['frozen_stage2']['accuracy_5pct'],
                      policy='record_only_do_not_abort')
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
        if args.experiment_arm=='decoder-only':
            # Bank distributions remain differentiable wrt no parameters.
            # Only the direct numerical decoder receives an optimizer update.
            primary.backward()
            norm=float(torch.linalg.vector_norm(torch.stack([
                p.grad.detach().float().norm() for p in system.head.parameters()
                if p.grad is not None])))
            gradients={'numeric_gradient_head_norm':norm,
                       'numeric_gradient_bank_norm':0.,
                       'gaussian_gradient_bank_norm_raw':0.,
                       'gaussian_gradient_multiplier':0.,
                       'gaussian_bank_bootstrap_used':0.,
                       'gaussian_gradient_bank_norm_applied':0.,
                       'gaussian_to_numeric_bank_gradient_ratio':0.,
                       'numeric_gaussian_bank_gradient_cosine':0.}
        else:
            gradients=numerical_first_backward(system,primary,auxiliary,
                auxiliary_gradient_ratio=args.auxiliary_gradient_ratio)
        total=primary+gradients['gaussian_gradient_multiplier']*auxiliary
        preclip=float(torch.nn.utils.clip_grad_norm_(trainable,1.,error_if_nonfinite=True))
        if not math.isfinite(preclip):
            raise FloatingPointError('LEARNABILITY_NONFINITE_GRADIENT_NORM')
        gradients['gradient_global_norm_before_clip']=preclip
        gradients['gradient_clip_fraction']=float(preclip>1.)
        gradients['gradient_clip_multiplier']=min(1.,1./max(preclip,1e-20))
        opt.step()
        # Check the updated parameters, not only pre-step gradients.
        if not all(bool(torch.isfinite(p).all()) for p in trainable):
            raise FloatingPointError('LEARNABILITY_NONFINITE_UPDATED_PARAMETERS')
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


def audit_only(args):
    payoffs,meta=mixture.certified_payoffs(args.mixture_fixture,args.teacher)
    report,train,dev=focus.plan(payoffs,mixture.original_counts(args.teacher,splits=('dev',)),
                         args.mixture_train_per_tier,args.mixture_new_dev_per_tier,args.seed)
    report['payoff_fixture_sha256']=meta['sha256']
    print(json.dumps(report,indent=2,sort_keys=True))
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refinement-checkpoint',type=Path,default=prior.DEFAULT_PARENT)
    p.add_argument('--bank-init-checkpoint',type=Path,default=None,
                   help='Optional old overflow-v2 or joint-head-v1 checkpoint: import only its first 16 banks (read-only)')
    p.add_argument('--decoder-init-checkpoint',type=Path,default=None,
                   help='Optional 21-bank v1 or 16-bank v3/v2 decoder checkpoint, copied read-only; reset optimizer')
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--mixture-fixture',type=Path,default=__import__('tools.nanojev_hawk_dove_million',fromlist=['DEFAULT_FIXTURE']).DEFAULT_FIXTURE,
                   help='Same immutable payoff fixture used to construct the original teacher')
    p.add_argument('--mixture-train-per-tier',type=int,default=900,
                   help='Distinct focused examples per tier, evenly divided among 1-10, 10-100, 100-1000')
    p.add_argument('--mixture-new-dev-per-tier',type=int,default=180,
                   help='Distinct focused held-out examples per tier, disjoint from training')
    p.add_argument('--mixture-strict-unique',action='store_true',
                   help='Fail closed if any requested quota requires repeated numerical instances')
    p.add_argument('--mixture-reallocate-empty-bands',action='store_true',
                   help='Opt in to moving an impossible magnitude quota to the closest smaller reachable band; report all percentage deviations')
    p.add_argument('--mixture-audit-only',action='store_true',
                   help='Audit three focused 1-10 / 10-100 / 100-1000 oracle quotas before GPU use')
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
    p.add_argument('--retention-fraction',type=float,default=0.,
                   help='Must be 0 in the focused learnability experiment; original accuracy is diagnostic only')
    p.add_argument('--max-original-dev-drop',type=float,default=.02,
                   help='Maximum original-dev accuracy drop vs frozen Stage 2 allowed for best checkpoint promotion')
    p.add_argument('--max-original-dev-collapse',type=float,default=.20,
                   help='Observation threshold only: log original-dev accuracy drop, never abort learnability training')
    p.add_argument('--required-new-dev-gain',type=float,default=0.,
                   help='Required new fractional held-out dev improvement vs frozen Stage 2 for promotion')
    p.add_argument('--experiment-arm',choices=('decoder-only','joint'),default='decoder-only',
                   help='A/B: train only numerical head or train head and all 21 banks')
    p.add_argument('--learnability-train-per-tier',type=int,default=0,
                   help='Must be 0: use EVERY generated focused training example, no implicit reduction')
    p.add_argument('--small-boost',type=float,default=2.)
    p.add_argument('--lr',type=float,default=.0001)
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


def main(argv=None):
    args=parser().parse_args(argv)
    return audit_only(args) if args.mixture_audit_only else run(args)

if __name__=='__main__':main()
