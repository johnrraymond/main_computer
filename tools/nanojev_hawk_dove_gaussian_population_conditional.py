#!/usr/bin/env python3
"""2,000-bank conditional Hawk–Dove X/Y density learner.

Research experiment. Train against SQLite teacher TRAIN presentations; select on
DEV; evaluate TEST once, after selecting the best DEV checkpoint. Inputs are
parsed *only from the prompt*, not from teacher row labels or instance metadata.
Fixed sparse Gaussian geometry is shared by X and Y; a trained prompt-conditioned
network predicts independent bank masses. This is a STRUCTURED NUMERIC INPUT
BASELINE, not a raw-token language model or a trained 21-bank checkpoint fork.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_gaussian_population_density as population

SCHEMA = 'nanojev.hawk-dove.gaussian-population-conditional.v1'
GATE_SCHEMA = 'nanojev.hawk-dove.gaussian-population-conditional.tier-numeric-gate.v1'
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_population_2000_conditional_v1'
INPUT_DIM = 7 + len(hd.TIERS)
TOTAL = 1_000_000


def _atomic_json(path: Path, data: dict):
    population._atomic_json(path, data)


def _atomic_save(path: Path, data: dict):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name+'.', suffix='.tmp',
                                     delete=False) as f:
        temp = Path(f.name)
        try:
            torch.save(data, f)
            f.flush(); os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def _atom_number(atom):
    if not isinstance(atom, str) or not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', atom):
        return None
    return float(atom)


def parse_visible(prompt: str, tier: str, representation: str) -> dict:
    """Recover only semantically identified numbers visible in this prompt.

    Payoff fixtures, row x/y labels, instance IDs, and DB a/b columns are never
    read by this extractor. Missing count inputs on T00 remain missing, not filled
    from oracle metadata. T01/T02 expose one raw oracle branch *in the prompt*.
    """
    if tier not in hd.TIERS:
        raise ValueError('CONDITIONAL_UNKNOWN_TIER')
    clean = hd.strip_and_validate_bars(prompt, representation)
    counts = {}
    raw = [None, None]
    if tier == 'T08':
        a = re.search(r'plays Hawk\s+(\d+)\s+out of a million times', clean)
        b = re.search(r'plays Dove\s+(\d+)\s+out of the same million times', clean)
        if a is None or b is None:
            raise ValueError('CONDITIONAL_T08_COUNT_NOT_VISIBLE')
        counts['a'], counts['b'] = int(a.group(1)), int(b.group(1))
    else:
        if '(ARGMAX' not in clean:
            raise ValueError('CONDITIONAL_MISSING_ARGMAX')
        tree = hd.parse_lisp(clean[clean.index('(ARGMAX'):])
        if not isinstance(tree, tuple) or len(tree)!=3 or tree[0]!='ARGMAX':
            raise ValueError('CONDITIONAL_BAD_ARGMAX')
        raw = [_atom_number(tree[1]), _atom_number(tree[2])]
        mapped = {}
        def walk(node):
            if not isinstance(node, tuple):
                return
            if len(node)==3 and node[0]=='MUL':
                first, second = node[1:]
                if isinstance(first,str) and first in hd.TAGS:
                    tag, val = first, _atom_number(second)
                elif isinstance(second,str) and second in hd.TAGS:
                    tag, val = second, _atom_number(first)
                else:
                    raise ValueError('CONDITIONAL_MUL_MISSING_TAG')
                if val is None or not float(val).is_integer():
                    raise ValueError('CONDITIONAL_MUL_COUNT_INVALID')
                if tag in mapped and mapped[tag]!=int(val):
                    raise ValueError('CONDITIONAL_MUL_COUNT_INCONSISTENT')
                mapped[tag] = int(val)
            for arg in node[1:]:
                walk(arg)
        walk(tree)
        hawk = [mapped[k] for k in (hd.TAGS[0], hd.TAGS[2]) if k in mapped]
        dove = [mapped[k] for k in (hd.TAGS[1], hd.TAGS[3]) if k in mapped]
        if hawk or dove:
            if not hawk or not dove or any(a != hawk[0] for a in hawk) or any(b != dove[0] for b in dove):
                raise ValueError('CONDITIONAL_MULTIPLE_COUNT_DISAGREEMENT')
            counts['a'], counts['b'] = hawk[0], dove[0]
        if tier=='T07':
            a = re.search(r'plays Hawk\s+(\d+)\s+out of a million times', clean)
            b = re.search(r'plays Dove\s+(\d+)\s+out of the same million times', clean)
            if not a or not b or counts != {'a':int(a.group(1)), 'b':int(b.group(1))}:
                raise ValueError('CONDITIONAL_T07_PROSE_DISAGREEMENT')
    if bool(counts) != (tier != 'T00'):
        raise ValueError('CONDITIONAL_REQUIRED_VISIBLE_COUNT_MISSING')
    if counts and (min(counts.values()) < 0 or sum(counts.values()) != TOTAL):
        raise ValueError('CONDITIONAL_INVALID_VISIBLE_COUNTS')
    expected_raw = {'T00':(True, True), 'T01':(True, False),
                    'T02':(False, True)}.get(tier,(False,False))
    if tuple(v is not None for v in raw)!=expected_raw:
        raise ValueError('CONDITIONAL_RAW_BRANCH_MISMATCH')
    return {'a': counts.get('a'), 'b': counts.get('b'), 'raw_x':raw[0], 'raw_y':raw[1],
            'tier':tier}


def make_features(extracted: dict, domain: tuple[tuple[float,float],tuple[float,float]]) -> list[float]:
    a = extracted['a']; b = extracted['b']; x = extracted['raw_x']; y = extracted['raw_y']
    def coordinate(v, interval):
        return (v-interval[0])/(interval[1]-interval[0]) if v is not None else 0.
    # Explicit availability flags prevent confusing absent zero with visible zero.
    values = [a/TOTAL if a is not None else 0., b/TOTAL if b is not None else 0.,
              float(a is not None), coordinate(x,domain[0]), coordinate(y,domain[1]),
              float(x is not None), float(y is not None)]
    values.extend(float(extracted['tier']==tier) for tier in hd.TIERS)
    return values


def train_domains(rows: list[dict], padding: float = 0.15):
    bounds=[]
    for key in ('x','y'):
        values=[float(row[key]) for row in rows]
        low, high=min(values), max(values)
        span=max(high-low, abs(high)*0.000001, 1.)
        bounds.append((low-padding*span, high+padding*span))
    return tuple(bounds)


def load_dataset(database: Path, device: torch.device):
    manifest=hd.db_manifest(database)
    rep=manifest['config'].get('representation',hd.REP_LEGACY)
    splits={s:hd.read_rows(database,s) for s in ('train','dev','test')}
    domain=train_domains(splits['train'])  # no DEV/TEST labels used for bounds
    result={}
    for split, rows in splits.items():
        if not rows:raise ValueError('CONDITIONAL_EMPTY_SPLIT:'+split)
        inputs=[]
        for row in rows:
            parsed=parse_visible(row['prompt'], row['tier'], rep)
            inputs.append(make_features(parsed, domain))
        result[split] = {
            'features':torch.tensor(inputs, dtype=torch.float32, device=device),
            'numeric':torch.tensor([[float(row['x']),float(row['y'])] for row in rows],
                                   dtype=torch.float32, device=device),
            'rows':rows,
            'tier_indices':{tier:torch.tensor([i for i,row in enumerate(rows) if row['tier']==tier],
                                              dtype=torch.long,device=device) for tier in hd.TIERS}}
    for key in ('dev','test'):
        for axis,(low,high) in enumerate(domain):
            raw=result[key]['numeric'][:,axis]
            if bool(((raw<low)|(raw>high)).any()):
                raise ValueError('CONDITIONAL_HELD_OUT_OUTSIDE_TRAIN_DOMAIN:'+key)
    # Teacher split is assigned by opponent-count instance, not by presentation;
    # confirm no instance is shared between TRAIN / DEV / TEST.
    sets=[{row['instance'] for row in splits[k]} for k in ('train','dev','test')]
    if (sets[0]&sets[1]) or (sets[0]&sets[2]) or (sets[1]&sets[2]):
        raise ValueError('CONDITIONAL_INSTANCE_SPLIT_LEAK')
    return result, domain, manifest, rep


class DensityNetwork(nn.Module):
    """Conditionally gate 2,000 simultaneous fixed Gaussian density experiments.

    A small network learns two continuous center proposals. Each bank receives a
    nonnegative weight based on its proximity to the proposed location plus a
    separately learned per-bank gain. This deliberately avoids learning an
    unconstrained 2,000-way classifier anew for every prompt.
    """
    def __init__(self, banks: int, bins: int, seed: int, *, hidden: int = 96):
        super().__init__()
        self.banks=banks; self.bins=bins; self.seed=seed; self.hidden=hidden
        indices,kernel=population.bank_geometry(banks,bins,seed,torch.device('cpu'))
        basis=torch.zeros((banks,bins),dtype=torch.float32)
        basis.scatter_add_(1,indices,kernel)
        self.register_buffer('basis',basis)
        generator=torch.Generator(device='cpu').manual_seed(seed)
        centers=torch.rand((banks,2),generator=generator)[:,0]
        self.register_buffer('centers',centers)
        self.encoder=nn.Sequential(nn.Linear(INPUT_DIM,hidden),nn.SiLU(),
                                   nn.Linear(hidden,hidden),nn.SiLU(),
                                   nn.Linear(hidden,2))
        self.bank_log_gains=nn.Parameter(torch.zeros((2,banks)))
        # 1.0 grid-bin kernel at initialization, learned from aggregate loss.
        self.log_proposal_sigma=nn.Parameter(torch.zeros(2))

    def locations(self,features):
        return self.encoder(features).sigmoid()

    def forward(self,features):
        location=self.locations(features)
        sigma_bins=self.log_proposal_sigma.exp().clamp(min=.3,max=20.)
        delta=(self.centers.reshape(1,1,-1)-location.unsqueeze(-1))*self.bins
        logits= -.5*(delta/sigma_bins.reshape(1,2,1)).square() + self.bank_log_gains
        weights=F.softmax(logits,dim=-1)
        density=weights.reshape(-1,self.banks) @ self.basis
        return density.reshape(-1,2,self.bins)


def normalized_target(numeric, domain, bins: int, sigma_bins: float):
    positions=(torch.arange(bins,device=numeric.device,dtype=torch.float32)+0.5)/bins
    values=torch.stack([(numeric[:,j]-domain[j][0])/(domain[j][1]-domain[j][0])
                        for j in range(2)],dim=1)
    deltas=(positions.reshape(1,1,-1)-values.unsqueeze(-1)) * (bins/sigma_bins)
    target=torch.exp(-0.5*deltas.square())
    return target/target.sum(dim=-1,keepdim=True).clamp_min(1e-20)


def numeric_decode(density, domain):
    bins=density.shape[-1]
    centers=(torch.arange(bins,device=density.device,dtype=torch.float32)+0.5)/bins
    locations=torch.stack([(density[:,j,:]@centers)*(domain[j][1]-domain[j][0])+domain[j][0]
                           for j in range(2)],dim=1)
    return locations


@torch.no_grad()
def evaluate(model, split, domain, sigma_bins: float, *, batch_size=256,
             relative_tolerance: float = 0.05):
    model.eval()
    all_preds=[]; all_numeric=[]; all_tvs=[]; all_kls=[]
    for start in range(0,len(split['rows']),batch_size):
        f=split['features'][start:start+batch_size]
        truth=split['numeric'][start:start+batch_size]
        density=model(f)
        target=normalized_target(truth,domain,model.bins,sigma_bins)
        tv=(density-target).abs().sum(dim=-1).mul(.5).mean(dim=-1)
        kl=(target*(target.clamp_min(1e-12).log()-density.clamp_min(1e-12).log())).sum(-1).mean(-1)
        all_preds.append(numeric_decode(density,domain).cpu())
        all_numeric.append(truth.cpu()); all_tvs.append(tv.cpu());all_kls.append(kl.cpu())
    p=torch.cat(all_preds); y=torch.cat(all_numeric)
    t=torch.cat(all_tvs); k=torch.cat(all_kls)
    if not math.isfinite(relative_tolerance) or not 0 < relative_tolerance < 1:
        raise ValueError('CONDITIONAL_INVALID_RELATIVE_TOLERANCE')
    abs_error=(p-y).abs()
    # Strict relative accuracy. Zero requires exactly zero error; no range-based
    # denominator can convert a near-zero oracle target into an automatic pass.
    hit=abs_error <= (relative_tolerance*y.abs())
    joint=hit.all(dim=-1)
    def score(mask):
        if int(mask.sum())==0:return None
        a=p[mask]; b=y[mask]
        pred_z=torch.sign(a[:,0]-a[:,1]); true_z=torch.sign(b[:,0]-b[:,1])
        hit_x=hit[mask,0];hit_y=hit[mask,1];both=joint[mask]
        ids=[row['instance'] for i,row in enumerate(split['rows']) if bool(mask[i])]
        # One success rate per independent count instance, not merely a tally
        # of its potentially many mutation-mask presentations.
        group={}
        for instance,ok in zip(ids,both.tolist()):
            group.setdefault(instance,[]).append(bool(ok))
        instance_rates=[sum(v)/len(v) for v in group.values()]
        return {'n':int(mask.sum()),'independent_instances':len(group),
                'within_tolerance':relative_tolerance,
                'x_within_tolerance':float(hit_x.float().mean()),
                'y_within_tolerance':float(hit_y.float().mean()),
                'xy_within_tolerance':float(both.float().mean()),
                'x_hits':int(hit_x.sum()),'y_hits':int(hit_y.sum()),
                'xy_hits':int(both.sum()),
                'instances_mean_xy_hit_rate':sum(instance_rates)/len(instance_rates),
                'instances_all_masks_xy_hit_rate':sum(r==1 for r in instance_rates)/len(instance_rates),
                'zero_x_count':int((y[mask,0]==0).sum()),
                'zero_y_count':int((y[mask,1]==0).sum()),
                'mean_tv':float(t[mask].mean()),
                'mean_kl':float(k[mask].mean()),
                'x_mae':float((a[:,0]-b[:,0]).abs().mean()),
                'y_mae':float((a[:,1]-b[:,1]).abs().mean()),
                'z_accuracy':float((pred_z==true_z).float().mean())}
    return {'overall':score(torch.ones(len(t),dtype=torch.bool)),
            'by_tier':{tier:score(torch.tensor([r['tier']==tier for r in split['rows']],dtype=torch.bool))
                       for tier in hd.TIERS}}



def tier_gate(evaluation: dict, *, min_rate: float = 0.80) -> dict:
    """Require both numerical outputs correct on the SAME presentation per tier."""
    if not math.isfinite(min_rate) or not (0 < min_rate <= 1):
        raise ValueError('CONDITIONAL_INVALID_GATE_MIN_RATE')
    tiers={}
    for tier in hd.TIERS:
        s=evaluation['by_tier'][tier]
        if s is None:raise ValueError('CONDITIONAL_GATE_EMPTY_TIER:'+tier)
        tiers[tier]={'n':s['n'], 'independent_instances':s['independent_instances'],
                     'x_rate':s['x_within_tolerance'],
                     'y_rate':s['y_within_tolerance'],
                     'xy_rate':s['xy_within_tolerance'],
                     'passes':s['xy_within_tolerance']+1e-12 >= min_rate}
    return {'target_rate':min_rate,'tolerance':evaluation['overall']['within_tolerance'],
            'min_tier_xy_rate':min(v['xy_rate'] for v in tiers.values()),
            'all_tiers_pass':all(v['passes'] for v in tiers.values()),
            'tiers':tiers}


@torch.no_grad()
def audit(args):
    """Read-only numerical certification of an existing frozen checkpoint.

    The checkpoint is NEVER changed; this command must not promote on TEST.
    """
    if args.device=='cuda' and not torch.cuda.is_available():
        raise ValueError('CONDITIONAL_CUDA_UNAVAILABLE')
    if not args.database.is_file():
        raise FileNotFoundError('CONDITIONAL_TEACHER_DATABASE_MISSING:'+str(args.database))
    if not math.isfinite(args.tolerance) or not (0<args.tolerance<1):
        raise ValueError('CONDITIONAL_INVALID_RELATIVE_TOLERANCE')
    if not math.isfinite(args.min_rate) or not (0<args.min_rate<=1):
        raise ValueError('CONDITIONAL_INVALID_GATE_MIN_RATE')
    if args.cpu_threads<1 or args.eval_batch_size<1:
        raise ValueError('CONDITIONAL_INVALID_AUDIT_DIMENSIONS')
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    device=torch.device(args.device)
    state=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    ident=state.get('identity')
    if state.get('schema')!=SCHEMA or not isinstance(ident,dict):
        raise ValueError('CONDITIONAL_CHECKPOINT_SCHEMA_MISMATCH')
    manifest=hd.db_manifest(args.database)
    if manifest['sha256'] != ident['db_sha256']:
        raise ValueError('CONDITIONAL_AUDIT_DATABASE_SHA_MISMATCH')
    dataset,domain,manifest,rep=load_dataset(args.database,device)
    if [list(x) for x in domain]!=ident['train_domains']:
        raise ValueError('CONDITIONAL_AUDIT_TRAIN_DOMAIN_MISMATCH')
    if rep!=ident['representation']:
        raise ValueError('CONDITIONAL_AUDIT_REPRESENTATION_MISMATCH')
    model=DensityNetwork(ident['bank_count'],ident['grid_bins'],ident['seed'],
                         hidden=ident['hidden']).to(device)
    model.load_state_dict(state['model']);model.eval()
    splits={}
    if not args.splits:
        raise ValueError('CONDITIONAL_INVALID_AUDIT_SPLITS')
    for split in args.splits.split(','):
        if split not in ('train','dev','test') or split in splits:
            raise ValueError('CONDITIONAL_INVALID_AUDIT_SPLITS')
        scores=evaluate(model,dataset[split],domain,ident['target_sigma_bins'],
                        batch_size=args.eval_batch_size,relative_tolerance=args.tolerance)
        splits[split]={'overall':scores['overall'],
                       'by_tier':scores['by_tier'],
                       'gate':tier_gate(scores,min_rate=args.min_rate)}
    result={'schema':GATE_SCHEMA,'status':'PASS' if all(s['gate']['all_tiers_pass'] for s in splits.values()) else 'FAIL',
            'checkpoint':str(args.checkpoint),'checkpoint_step':int(state['step']),
            'database':str(args.database),'database_sha256':manifest['sha256'],
            'bank_count':ident['bank_count'],'threshold':'abs(pred-truth) <= tolerance * abs(truth); zero requires exact',
            'min_rate':args.min_rate,'tolerance':args.tolerance,'splits':splits,
            'note':'Read-only audit; TEST outcomes must not be used for model selection or tuning.'}
    if args.output is not None:
        _atomic_json(args.output,result)
    for split,item in splits.items():
        print(f'\n{split.upper()} {ident["bank_count"]:,}-bank T00-T08 numeric gate '
              f'(within {100*args.tolerance:g}% for BOTH X and Y; target {100*args.min_rate:g}%):')
        for tier,g in item['gate']['tiers'].items():
            print(f'  {tier}: X={100*g["x_rate"]:5.1f}% Y={100*g["y_rate"]:5.1f}% '
                  f'BOTH={100*g["xy_rate"]:5.1f}% '
                  f'({g["n"]} presentations, {g["independent_instances"]} instances) '
                  f'{"PASS" if g["passes"] else "FAIL"}')
        print(f'  ALL TIERS: {"PASS" if item["gate"]["all_tiers_pass"] else "FAIL"}')
    print('TIER_GATE_STATUS='+result['status'],flush=True)
    return result


def _identity(args, manifest, domain):
    return {'schema':SCHEMA,'bank_count':args.banks,'grid_bins':args.grid_bins,
            'seed':args.seed,'hidden':args.hidden,'target_sigma_bins':args.target_sigma_bins,
            'db_sha256':manifest['sha256'],'db_schema':manifest['config']['schema'],
            'train_domains':[list(v) for v in domain],
            'representation':manifest['config'].get('representation',hd.REP_LEGACY),
            'view_cases':args.view_cases,'selection':args.selection,
            'steps':args.steps,'probe_every':args.probe_every,'batch_size':args.batch_size,
            'learning_rate':args.lr,'min_learning_rate':args.min_lr,'device':args.device,
            'objective':'prompt_conditioned_two_distributions_target_mass_KL_MSE_center',
            'inputs':'prompt-visible parsed numeric literals and explicit tier',
            'geometry':'fixed_shared_gaussian_basis_learned_radial_proposal_bank_gains',
            'data_policy':'TRAIN gradients DEV checkpoint selection TEST final once'}


def _validate_args(args):
    if (args.banks<2 or args.grid_bins<32 or args.hidden<4 or args.steps<1
            or args.probe_every<1 or args.batch_size<1 or args.eval_batch_size<1
            or args.cpu_threads<1 or args.view_cases<0):
        raise ValueError('CONDITIONAL_INVALID_DIMENSIONS')
    if not (math.isfinite(args.lr) and math.isfinite(args.min_lr) and 0<args.min_lr<=args.lr<=1):
        raise ValueError('CONDITIONAL_INVALID_LEARNING_RATES')
    if not math.isfinite(args.min_rate) or not (0 < args.min_rate <= 1):
        raise ValueError('CONDITIONAL_INVALID_GATE_MIN_RATE')
    if args.target_sigma_bins<=0 or not math.isfinite(args.target_sigma_bins):
        raise ValueError('CONDITIONAL_INVALID_TARGET_SIGMA')
    if args.device=='cuda' and not torch.cuda.is_available():
        raise ValueError('CONDITIONAL_CUDA_UNAVAILABLE')


@torch.no_grad()
def _view_cases(model, dev, domain, args, output, step):
    """Plot identical held-out prompts across steps. Never backprop from plots."""
    if args.view_cases == 0:return
    # Prefer numerical tiers without exposed oracle results for demonstration.
    chosen=[]
    for tier in ('T08','T03','T06','T00','T01','T02','T04','T05','T07'):
        for index,row in enumerate(dev['rows']):
            if row['tier']==tier:
                chosen.append((index,tier));break
        if len(chosen)>=args.view_cases:break
    model.eval()
    grid=model.basis.mean(dim=0).detach().cpu()
    for case_num,(index,tier) in enumerate(chosen):
        features=dev['features'][index:index+1]
        truth=normalized_target(dev['numeric'][index:index+1],domain,model.bins,
                                args.target_sigma_bins)[0].cpu()
        pred=model(features)[0].cpu()
        for axis,label in enumerate(('X','Y')):
            folder=output/'dev_density_views'/f'case-{case_num:02d}-{tier}-{label}'
            population._write_curve(folder/f'step-{step:06d}.csv',truth[axis],grid,pred[axis])
            population._write_svg(folder/f'step-{step:06d}.svg',truth[axis],grid,pred[axis],
                                  banks=args.banks,step=step)


def _checkpoint(model,opt,scheduler,step,best_tv,ident,generator,best_rank=None):
    return {'schema':SCHEMA,'identity':ident,'step':step,'best_dev_tv':best_tv,
            'best_gate_rank':best_rank,
            'model':model.state_dict(),'optimizer':opt.state_dict(),
            'scheduler':scheduler.state_dict(),'generator_state':generator.get_state()}


def train(args):
    _validate_args(args)
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    device=torch.device(args.device)
    if not args.database.is_file():
        raise FileNotFoundError('CONDITIONAL_TEACHER_DATABASE_MISSING:'+str(args.database))
    dataset, domain, manifest, rep=load_dataset(args.database,device)
    output=args.output
    ident=_identity(args,manifest,domain)
    if (output/'config.json').exists():
        if json.loads((output/'config.json').read_text('utf8'))!=ident:
            raise ValueError('CONDITIONAL_CONFIG_DRIFT:'+str(output))
        if (output/'summary.json').exists():
            raise ValueError('CONDITIONAL_ALREADY_COMPLETE_USE_NEW_OUTPUT')
        if not args.resume:
            raise ValueError('CONDITIONAL_PARTIAL_REQUIRES_RESUME')
    elif output.exists() and any(output.iterdir()):
        raise ValueError('CONDITIONAL_UNKNOWN_EXISTING_OUTPUT')
    else:
        output.mkdir(parents=True,exist_ok=True)
        _atomic_json(output/'config.json',ident)
    model=DensityNetwork(args.banks,args.grid_bins,args.seed,hidden=args.hidden).to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=0.)
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=args.steps,eta_min=args.min_lr)
    generator=torch.Generator(device='cpu').manual_seed(args.seed+91)
    best_tv=math.inf; best_rank=None; start=0
    if args.resume:
        state=torch.load(output/'latest.pt',map_location=device,weights_only=True)
        if state.get('identity')!=ident or state.get('schema')!=SCHEMA:
            raise ValueError('CONDITIONAL_RESUME_IDENTITY_MISMATCH')
        model.load_state_dict(state['model']);opt.load_state_dict(state['optimizer'])
        scheduler.load_state_dict(state['scheduler']);generator.set_state(state['generator_state'])
        start=int(state['step']);best_tv=float(state['best_dev_tv'])
        best_rank=tuple(state['best_gate_rank']) if state.get('best_gate_rank') is not None else None
    history=[]
    if (output/'history.json').exists():history=json.loads((output/'history.json').read_text())['records']
    pools=dataset['train']['tier_indices']
    def checkpoint(step, train_loss=None):
        nonlocal best_tv,best_rank
        dev=evaluate(model,dataset['dev'],domain,args.target_sigma_bins,batch_size=args.eval_batch_size)
        tv=dev['overall']['mean_tv']
        if not math.isfinite(tv):raise FloatingPointError('CONDITIONAL_NONFINITE_DEV')
        gate=tier_gate(dev,min_rate=args.min_rate)
        # Gate-based selection minimizes the weakest tier, not the dominant
        # presentation-weighted average. Never use TEST for checkpoint choice.
        rank=(gate['min_tier_xy_rate'],
              sum(x['xy_rate'] for x in gate['tiers'].values())/len(hd.TIERS),-tv)
        improved=(tv<best_tv) if args.selection=='dev-tv' else (best_rank is None or rank>best_rank)
        if improved:
            best_tv=tv
            best_rank=rank
        state=_checkpoint(model,opt,scheduler,step,best_tv,ident,generator,best_rank)
        if improved:_atomic_save(output/'best.pt',state)
        _atomic_save(output/'latest.pt',state)
        _view_cases(model,dataset['dev'],domain,args,output,step)
        record={'step':step,'train_loss':train_loss,'dev':dev,'dev_tier_gate':gate,'best_dev_tv':best_tv,
                'promoted':improved,'selection':args.selection,'lr':opt.param_groups[0]['lr']}
        history.append(record)
        _atomic_json(output/'history.json',{'schema':SCHEMA,'records':history})
        print(f'conditional banks={args.banks} step={step}/{args.steps} '
              f'dev_tv={tv:.6f} dev_Z={dev["overall"]["z_accuracy"]:.4f} '
              f'best_tv={best_tv:.6f} min_tier_xy5={gate["min_tier_xy_rate"]:.4f} '
              f'gate={"PASS" if gate["all_tiers_pass"] else "FAIL"}',flush=True)
    if start==0:checkpoint(0)
    started=time.monotonic()
    for step in range(start+1,args.steps+1):
        model.train()
        # Balanced presentation sampling across tiers; all indices are TRAIN.
        selected=[]
        for j,tier in enumerate(hd.TIERS):
            count=args.batch_size//len(hd.TIERS)+int(j < args.batch_size%len(hd.TIERS))
            if count==0:continue
            pool=pools[tier]
            if not len(pool):raise ValueError('CONDITIONAL_EMPTY_TRAIN_TIER:'+tier)
            draws=torch.randint(len(pool),(count,),generator=generator)
            selected.append(pool[draws.to(device)])
        ix=torch.cat(selected)
        inputs=dataset['train']['features'][ix]
        numeric=dataset['train']['numeric'][ix]
        target=normalized_target(numeric,domain,args.grid_bins,args.target_sigma_bins)
        opt.zero_grad(set_to_none=True)
        density=model(inputs)
        kl=(target*(target.clamp_min(1e-12).log()-density.clamp_min(1e-12).log())).sum(-1).mean()
        mse=args.grid_bins*(density-target).square().mean()
        target_locations=torch.stack([(numeric[:,j]-domain[j][0])/(domain[j][1]-domain[j][0])
                                      for j in range(2)],dim=1)
        center_loss=(model.locations(inputs)-target_locations).square().mean()
        loss=kl + mse + 10.0*center_loss
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('CONDITIONAL_NONFINITE_TRAIN_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.0,error_if_nonfinite=True)
        opt.step(); scheduler.step()
        if step%args.probe_every==0 or step==args.steps:
            checkpoint(step,float(loss.detach()))
    best=torch.load(output/'best.pt',map_location=device,weights_only=True)
    model.load_state_dict(best['model'])
    test=evaluate(model,dataset['test'],domain,args.target_sigma_bins,batch_size=args.eval_batch_size)
    train_score=evaluate(model,dataset['train'],domain,args.target_sigma_bins,
                         batch_size=args.eval_batch_size)
    summary={'schema':SCHEMA,'status':'COMPLETE','identity':ident,
             'input_mode':'structured_prompt_visible_numeric_features_not_raw_text_transformer',
             'bank_count_per_output':args.banks,'outputs':['X','Y'],
             'trainable_parameters':sum(p.numel() for p in model.parameters()),
             'fixed_gaussian_geometry':True,'uses_test_for_training_or_promotion':False,
             'best_step':int(best['step']),'best_dev_tv':best_tv,
             'checkpoint_selection':args.selection,
             'best_train':train_score,'best_dev':evaluate(model,dataset['dev'],domain,args.target_sigma_bins),
             'test_once_after_best_selection':test,
             'split_sizes':{k:len(v['rows']) for k,v in dataset.items()},
             'independent_split_instances':True,
             'elapsed_seconds':round(time.monotonic()-started,3),
             'limitations':['oracle X/Y distributions used as TRAIN supervision',
                 'teacher payoff fixture is fixed; generalization to unseen payoff matrices not tested',
                 'structured parser reads numeric literals; raw-token understanding not established',
                 'T00-T02 contain prompt-visible oracle branches; assess T03-T08 separately',
                 'Gaussian positions/widths fixed; radial proposal locations and amplitude gains trained']}
    summary['numeric_goal']={
        'rule':'For each tier at least 80% of presentations have BOTH X and Y within 5% of the nonzero oracle magnitude; zero requires exact.',
        'tolerance':0.05,'min_rate':args.min_rate,
        'dev':tier_gate(summary['best_dev'],min_rate=args.min_rate),
        'test':tier_gate(summary['test_once_after_best_selection'],min_rate=args.min_rate)}
    _atomic_json(output/'summary.json',summary)
    print(json.dumps({'best_step':summary['best_step'],'best_dev':summary['best_dev']['overall'],
                      'test':test['overall'],'test_by_tier':test['by_tier'],
                      'summary':str(output/'summary.json')},indent=2),flush=True)
    return summary


def predict(args):
    state=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    ident=state.get('identity')
    if state.get('schema')!=SCHEMA or not ident:
        raise ValueError('CONDITIONAL_CHECKPOINT_SCHEMA_MISMATCH')
    device=torch.device(args.device)
    if args.device=='cuda' and not torch.cuda.is_available():raise ValueError('CONDITIONAL_CUDA_UNAVAILABLE')
    model=DensityNetwork(ident['bank_count'],ident['grid_bins'],ident['seed'],
                         hidden=ident['hidden']).to(device)
    model.load_state_dict(state['model']);model.eval()
    domain=tuple(tuple(x) for x in ident['train_domains'])
    if args.representation!=ident['representation']:
        raise ValueError('CONDITIONAL_PREDICT_REPRESENTATION_MISMATCH')
    prompt=args.prompt_file.read_text('utf-8')
    visible=parse_visible(prompt,args.tier,args.representation)
    features=torch.tensor([make_features(visible,domain)],device=device,dtype=torch.float32)
    with torch.no_grad():
        density=model(features)[0]
        xy=numeric_decode(density.unsqueeze(0),domain)[0].cpu().tolist()
    answer={'schema':SCHEMA+'.prediction','checkpoint':str(args.checkpoint),
            'step':int(state['step']),'tier':args.tier,
            'predicted_X':xy[0],'predicted_Y':xy[1],
            'predicted_Z':'HAWK' if xy[0]>xy[1] else 'DOVE' if xy[0]<xy[1] else 'TIE',
            'visible_inputs':visible,'oracle_labels_read':False,
            'warning':'Structured prompt numeric baseline, not raw-token inference'}
    print(json.dumps(answer,indent=2),flush=True)
    return answer


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest='command',required=True)
    t=subs.add_parser('train',help='Train 2000 shared Gaussian banks conditioned on actual teacher prompts')
    t.add_argument('--database',type=Path,default=hd.DEFAULT_DB)
    t.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    t.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    t.add_argument('--banks',type=int,default=2000)
    t.add_argument('--grid-bins',type=int,default=256)
    t.add_argument('--target-sigma-bins',type=float,default=2.5)
    t.add_argument('--hidden',type=int,default=96)
    t.add_argument('--batch-size',type=int,default=64)
    t.add_argument('--eval-batch-size',type=int,default=256)
    t.add_argument('--steps',type=int,default=15000)
    t.add_argument('--probe-every',type=int,default=500)
    t.add_argument('--min-rate',type=float,default=0.80,
                   help='Per-tier joint-X/Y within-5%% gate target')
    t.add_argument('--selection',choices=('dev-tv','tier-gate'),default='dev-tv',
                   help='Checkpoint selection by DEV density TV or weakest-tier 5%% X/Y accuracy')
    t.add_argument('--view-cases',type=int,default=2,
                   help='Fixed DEV cases whose X/Y density reconstructions are plotted each probe')
    t.add_argument('--lr',type=float,default=0.003)
    t.add_argument('--min-lr',type=float,default=0.00003)
    t.add_argument('--seed',type=int,default=20261021)
    t.add_argument('--cpu-threads',type=int,default=4)
    t.add_argument('--resume',action='store_true')
    a=subs.add_parser('audit',help='Read-only 80%%-within-5%% per-tier X/Y gate on saved checkpoint')
    a.add_argument('--checkpoint',type=Path,required=True)
    a.add_argument('--database',type=Path,default=hd.DEFAULT_DB)
    a.add_argument('--output',type=Path,default=None)
    a.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    a.add_argument('--splits',default='dev,test',help='Comma-separated train,dev,test; TEST must not select models')
    a.add_argument('--tolerance',type=float,default=0.05)
    a.add_argument('--min-rate',type=float,default=0.80)
    a.add_argument('--eval-batch-size',type=int,default=256)
    a.add_argument('--cpu-threads',type=int,default=4)
    q=subs.add_parser('predict',help='Predict X/Y/Z from a prompt without teacher labels')
    q.add_argument('--checkpoint',type=Path,required=True)
    q.add_argument('--tier',choices=hd.TIERS,required=True)
    q.add_argument('--prompt-file',type=Path,required=True)
    q.add_argument('--representation',choices=hd.REPRESENTATIONS,default=hd.REP_LEGACY)
    q.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return train(args) if args.command=='train' else audit(args) if args.command=='audit' else predict(args)

if __name__=='__main__':main()
