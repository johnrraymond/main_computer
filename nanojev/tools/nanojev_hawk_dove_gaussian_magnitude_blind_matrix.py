#!/usr/bin/env python3
"""V4 Gaussian magnitude experiment restoring supervised continuous location learning.

Separate research lineage: original Gaussian continuous coordinate regression,
nonuniform small-dense positive-magnitude Gaussian banks, LOCAL barycentric
activation so tails cannot corrupt small answers, and categorical sign/zero.
X/Y are decoded from the Gaussian bank population's analytical means; there
is no direct signed-coordinate numerical output bypass. The oracle-blind data
splits, prompt-only inputs, and strict held-out certification are unchanged.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import time

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as old
from tools import nanojev_hawk_dove_gaussian_population_conditional as base

SCHEMA = 'nanojev.hawk-dove.gaussian-magnitude-matrix-holdout.v4'
DEFAULT_OUTPUT = Path.home()/'NanoJev/runs/hawk_dove_gaussian_magnitude_2000_blind_matrix_v4'
# Greater than 1 crowds bank centers into the smaller-magnitude decades.
DEFAULT_DENSITY_POWER = 1.65
DEFAULT_MIN_MAGNITUDE = 1e-5
DEFAULT_GAUSSIAN_CV = 0.06


def magnitude_geometry(banks, bins, min_mag, max_mag, density_power,
                       gaussian_cv=DEFAULT_GAUSSIAN_CV):
    """Normalized Gaussian kernels on a small-dense unsigned magnitude axis.

    For numerical decoding the means are the untruncated Gaussian centers;
    the strictly nonnegative density display is normalized over positive bins.
    Sign and exact zero are separate atoms, not part of the Gaussian density.
    """
    if (banks < 8 or bins < 32 or not 0 < min_mag < max_mag or
        density_power <= 1 or not 0 < gaussian_cv < 1 or
        not all(map(math.isfinite,(min_mag,max_mag,density_power,gaussian_cv)))):
        raise ValueError('GAUSSIAN_MAG_INVALID_GEOMETRY')
    u=torch.linspace(0,1,banks,dtype=torch.float64)
    low,high=math.log10(min_mag),math.log10(max_mag)
    logs=low+(high-low)*u.pow(density_power)
    centers=10.**logs
    sigmas=centers*gaussian_cv
    edges=10.**torch.linspace(low-2,high+1,bins+1,dtype=torch.float64)
    z=(edges[None,:]-centers[:,None])/sigmas[:,None]
    cdf=torch.special.ndtr(z)
    mass=(cdf[:,1:]-cdf[:,:-1]).clamp_min(0)
    mass=mass/mass.sum(-1,keepdim=True).clamp_min(1e-200)
    if (not bool(torch.isfinite(mass).all()) or
        bool((mass.sum(-1)-1).abs().max()>1e-8)):
        raise ArithmeticError('GAUSSIAN_MAG_UNNORMALIZED_KERNELS')
    return centers.float(),logs.float(),sigmas.float(),mass.float(),edges.float()


class GaussianMagnitudeNetwork(nn.Module):
    """Continuous signed-location learning drives LOCAL Gaussian bank populations."""
    def __init__(self,banks,bins,hidden,seed,min_mag,max_mag,density_power,
                 gaussian_cv=DEFAULT_GAUSSIAN_CV):
        super().__init__()
        self.banks=banks;self.bins=bins;self.min_mag=min_mag;self.max_mag=max_mag
        self.density_power=density_power;self.gaussian_cv=gaussian_cv
        centers,logs,sigmas,basis,edges=magnitude_geometry(
            banks,bins,min_mag,max_mag,density_power,gaussian_cv)
        self.register_buffer('bank_means',centers)
        self.register_buffer('bank_log_means',logs)
        self.register_buffer('bank_sigma',sigmas)
        self.register_buffer('basis',basis)
        self.register_buffer('magnitude_edges',edges)
        # Old Gaussian experiment's continuous supervised numerical position,
        # plus independent negative/EXACT ZERO/positive classifications.
        self.encoder=nn.Sequential(nn.Linear(old.INPUT_DIM,hidden),nn.SiLU(),
                                   nn.Linear(hidden,hidden),nn.SiLU(),
                                   nn.Linear(hidden,8))

    def decode_population(self,weights,sign_class):
        """Numerical output is the analytical mean of active Gaussian banks."""
        magnitude=(weights*self.bank_means).sum(-1)
        sign=torch.where(sign_class==0,-1.,torch.where(sign_class==2,1.,0.))
        return magnitude*sign,magnitude

    def activate_local_banks(self,desired):
        """Only two bracketing centers can activate; no distant tail leakage."""
        desired=desired.clamp(self.min_mag,self.max_mag)
        right=torch.searchsorted(self.bank_means.contiguous(),desired.contiguous())
        right=right.clamp(1,self.banks-1);left=right-1
        lo=self.bank_means[left];hi=self.bank_means[right]
        frac=((desired-lo)/(hi-lo)).clamp(0.,1.)
        weights=torch.zeros((*desired.shape,self.banks),device=desired.device,dtype=desired.dtype)
        weights.scatter_(-1,left.unsqueeze(-1),(1.-frac).unsqueeze(-1))
        weights.scatter_(-1,right.unsqueeze(-1),frac.unsqueeze(-1))
        return weights

    def forward(self,features,*,with_density=True):
        head=self.encoder(features)
        # Restore original Gaussian's continuous signed coordinate predictor.
        # It is independently supervised; unlike V2 it is not a log10 regressor.
        signed_location=head[:,:2].tanh()*self.max_mag
        sign_logits=head[:,2:].reshape(-1,2,3)
        weights=self.activate_local_banks(signed_location.abs())
        cls=sign_logits.argmax(-1)
        numeric,magnitude=self.decode_population(weights,cls)
        out={'numeric':numeric,'magnitude':magnitude,'weights':weights,
             'signed_location':signed_location,'sign_logits':sign_logits,
             'sign_class':cls}
        if with_density:
            out['density']=(weights.reshape(-1,self.banks) @ self.basis).reshape(-1,2,self.bins)
        return out


def gaussian_target(abs_numeric,zero_mask,model):
    """Ground-truth Gaussian density; never used as an inference input."""
    m=abs_numeric.to(torch.float64).clamp(model.min_mag,model.max_mag)
    sigma=(m*model.gaussian_cv).clamp_min(model.min_mag*model.gaussian_cv)
    edges=model.magnitude_edges.to(torch.float64)
    z=(edges[None,None,:]-m[:,:,None])/sigma[:,:,None]
    cdf=torch.special.ndtr(z)
    mass=(cdf[:,:,1:]-cdf[:,:,:-1]).clamp_min(0)
    mass=mass/mass.sum(-1,keepdim=True).clamp_min(1e-200)
    if not bool(torch.isfinite(mass).all()):
        raise ArithmeticError('GAUSSIAN_MAG_INVALID_TARGET_DENSITY')
    return mass.to(dtype=abs_numeric.dtype)


def loss_and_scores(model,features,oracle,geometry_loss=True):
    out=model(features,with_density=geometry_loss)
    zero=oracle==0
    cls=torch.where(zero,1,torch.where(oracle<0,0,2)).long()
    sign_ce=F.cross_entropy(out['sign_logits'].reshape(-1,3),cls.reshape(-1),
               weight=torch.tensor([1.,4.,1.],device=oracle.device))
    # Restore the successful signed continuous-coordinate supervision of
    # the original Gaussian. This remains the primary location-learning loss.
    target_signed=(oracle/model.max_mag).clamp(-1.,1.)
    location_loss=F.mse_loss(out['signed_location']/model.max_mag,target_signed)
    active=(~zero).float()
    # Diagnostic-only: do not optimize the unsigned log error against a signed
    # coordinate (it previously pulled signed locations to the wrong side).
    with torch.no_grad():
        target_log=oracle.abs().clamp_min(model.min_mag).log10()
        err=out['magnitude'].clamp_min(model.min_mag).log10()-target_log
        magnitude_log=F.smooth_l1_loss(err,torch.zeros_like(err),reduction='none')
        numeric_loss=(magnitude_log*active).sum()/active.sum().clamp_min(1.)
    gaussian_kl=torch.zeros((),device=oracle.device)
    if geometry_loss:
        target=gaussian_target(oracle.abs(),zero,model)
        density=out['density'].clamp_min(1e-12)
        kl=(target*(target.clamp_min(1e-12).log()-density.log())).sum(-1)
        gaussian_kl=(kl*active).sum()/active.sum().clamp_min(1.)
    return sign_ce+20.*location_loss+.02*gaussian_kl,{
        'sign_ce':sign_ce,'location_mse':location_loss,
        'numeric_log_loss':numeric_loss,'gaussian_kl':gaussian_kl}


@torch.no_grad()
def evaluate(model,split,*,batch_size=256,tolerance=.05):
    model.eval()
    preds=[];targets=[];tvs=[];kls=[]
    for start in range(0,len(split['rows']),batch_size):
        inp=split['features'][start:start+batch_size]
        truth=split['numeric'][start:start+batch_size]
        out=model(inp)
        target=gaussian_target(truth.abs(),truth==0,model)
        tv=(out['density']-target).abs().sum(-1)*.5
        kl=(target*(target.clamp_min(1e-12).log()-out['density'].clamp_min(1e-12).log())).sum(-1)
        nonzero=truth!=0
        denom=nonzero.sum(-1).clamp_min(1)
        tv=(tv*nonzero).sum(-1)/denom
        kl=(kl*nonzero).sum(-1)/denom
        preds.append(out['numeric'].cpu());targets.append(truth.cpu());tvs.append(tv.cpu());kls.append(kl.cpu())
    p=torch.cat(preds); y=torch.cat(targets);tv=torch.cat(tvs);kl=torch.cat(kls)
    hit=(p-y).abs()<=tolerance*y.abs()
    both=hit.all(-1)
    def score(mask):
        idx=torch.nonzero(mask).view(-1).tolist()
        if not idx:return None
        ids={}
        for i in idx:
            ids.setdefault(split['rows'][i]['instance'],[]).append(bool(both[i]))
        good=sum(all(q) for q in ids.values())
        true_sign=torch.sign(y[mask,0]-y[mask,1]);pred_sign=torch.sign(p[mask,0]-p[mask,1])
        return {'n':len(idx),'independent_instances':len(ids),
                'within_tolerance':tolerance,'x_within_tolerance':float(hit[mask,0].float().mean()),
                'y_within_tolerance':float(hit[mask,1].float().mean()),
                'xy_within_tolerance':float(both[mask].float().mean()),
                'x_hits':int(hit[mask,0].sum()),'y_hits':int(hit[mask,1].sum()),
                'xy_hits':int(both[mask].sum()),
                'instances_all_masks_xy_hit_rate':good/len(ids),
                'instances_mean_xy_hit_rate':sum(sum(v)/len(v) for v in ids.values())/len(ids),
                'zero_x_count':int((y[mask,0]==0).sum()),'zero_y_count':int((y[mask,1]==0).sum()),
                'mean_tv':float(tv[mask].mean()),'mean_kl':float(kl[mask].mean()),
                'x_mae':float((p[mask,0]-y[mask,0]).abs().mean()),
                'y_mae':float((p[mask,1]-y[mask,1]).abs().mean()),
                'z_accuracy':float((true_sign==pred_sign).float().mean())}
    return {'overall':score(torch.ones(len(y),dtype=torch.bool)),
            'by_tier':{t:score(torch.tensor([row['tier']==t for row in split['rows']]))
                       for t in old.TIERS}}


def identity(args,manifest,domain):
    return {'schema':SCHEMA,'seed':args.seed,'banks':args.banks,'grid_bins':args.grid_bins,
            'hidden':args.hidden,'min_mag':args.min_mag,
            'density_power':args.density_power,'gaussian_cv':DEFAULT_GAUSSIAN_CV,
            'train_matrices':args.train_matrices,'dev_matrices':args.dev_matrices,
            'test_matrices':args.test_matrices,'counts_per_matrix':args.counts_per_matrix,
            'masks':args.masks,'payoff_bound':args.payoff_bound,'steps':args.steps,
            'probe_every':args.probe_every,'batch_size':args.batch_size,
            'lr':args.lr,'min_lr':args.min_lr,'tolerance':args.tolerance,
            'min_rate':args.min_rate,'domain':[list(x) for x in domain],
            'matrix_hashes':manifest['matrix_hashes'],
            'bank_geometry':'small-dense log10 magnitude centers, Gaussian kernels',
            'population_contract':'supervised continuous signed location activates adjacent Gaussian magnitude banks',
            'decode':'localized Gaussian-bank analytical mean + categorical sign/zero',
            'input_contract':'visible counts + payoff bindings only',
            'splitting':'full payoff-matrix identity, held out by train/dev/test',
            'zero':'separate categorical atom, exact numerical 0',
            'tiers':'T01B,T02B,T03,T04,T05,T06,T07,T08',
            'data_policy':'TRAIN gradients; DEV promotion; TEST frozen evaluation once'}


def _validate(args):
    if (args.banks<8 or args.grid_bins<32 or args.hidden<4 or args.steps<1 or
        args.probe_every<1 or args.batch_size<8 or args.eval_batch_size<1 or args.cpu_threads<1 or
        args.payoff_bound<1 or args.counts_per_matrix<3 or args.masks not in (1,2) or
        min(args.train_matrices,args.dev_matrices,args.test_matrices)<1 or
        not 0<args.min_mag<1 or args.density_power<=1):
        raise ValueError('GAUSSIAN_MAG_POP_INVALID_CONFIGURATION')
    if not (0<args.min_lr<=args.lr<=1 and 0<args.tolerance<1 and 0<args.min_rate<=1):
        raise ValueError('GAUSSIAN_MAG_POP_INVALID_TRAINING_POLICY')
    if args.device=='cuda' and not torch.cuda.is_available():raise ValueError('GAUSSIAN_MAG_CUDA_UNAVAILABLE')
    if args.payoff_bound>9:raise ValueError('GAUSSIAN_MAG_PAYOFF_BINDINGS_OUT_OF_RANGE')


def _make_model(ident,device):
    return GaussianMagnitudeNetwork(ident['banks'],ident['grid_bins'],ident['hidden'],
              ident['seed'],ident['min_mag'],1.25*old.TOTAL*ident['payoff_bound'],
              ident['density_power']).to(device)


def train(args):
    _validate(args)
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    device=torch.device(args.device)
    data,domain,manifest=old._dataset(args,device)
    ident=identity(args,manifest,domain)
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError('GAUSSIAN_MAG_OUTPUT_EXISTS_NO_OVERWRITE:'+str(args.output))
    args.output.mkdir(parents=True,exist_ok=True)
    base._atomic_json(args.output/'config.json',ident)
    base._atomic_json(args.output/'matrix_split_manifest.json',manifest)
    model=_make_model(ident,device)
    opt=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=0.)
    schedule=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=args.steps,eta_min=args.min_lr)
    generator=torch.Generator().manual_seed(args.seed+91)
    pools=data['train']['tier_indices']
    best_rank=None;best_step=0;history=[]
    started=time.monotonic()
    def probe(step,loss=None):
        nonlocal best_rank,best_step
        dev=evaluate(model,data['dev'],batch_size=args.eval_batch_size,tolerance=args.tolerance)
        rank=old._rank(dev);gate=old._gate(dev,args.min_rate)
        promoted=best_rank is None or rank>best_rank
        if promoted:
            best_rank=rank;best_step=step
            base._atomic_save(args.output/'best.pt',{'schema':SCHEMA,'identity':ident,
                'step':step,'model':model.state_dict()})
        history.append({'step':step,'train_loss':loss,'dev_gate':gate,
                        'dev':dev['overall'],'promoted':promoted,'lr':opt.param_groups[0]['lr']})
        base._atomic_json(args.output/'history.json',{'schema':SCHEMA,'records':history})
        print(f'gaussian-magnitude banks={args.banks} step={step}/{args.steps} '
              f'dev_min_joint5={rank[0]:.4f} dev_gaussian_tv={dev["overall"]["mean_tv"]:.6f} '
              f'gate={gate["status"]}',flush=True)
    probe(0)
    for step in range(1,args.steps+1):
        model.train();chosen=[]
        for j,tier in enumerate(old.TIERS):
            count=args.batch_size//len(old.TIERS)+int(j<args.batch_size%len(old.TIERS))
            pool=pools[tier]
            chosen.append(pool[torch.randint(len(pool),(count,),generator=generator).to(device)])
        ids=torch.cat(chosen)
        inp=data['train']['features'][ids]
        y=data['train']['numeric'][ids]
        opt.zero_grad(set_to_none=True)
        loss,_=loss_and_scores(model,inp,y)
        if not bool(torch.isfinite(loss)):raise FloatingPointError('GAUSSIAN_MAG_NONFINITE_TRAIN_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
        opt.step();schedule.step()
        if step%args.probe_every==0 or step==args.steps:probe(step,float(loss.detach()))
    state=torch.load(args.output/'best.pt',map_location=device,weights_only=True)
    model.load_state_dict(state['model']);model.eval()
    dev=evaluate(model,data['dev'],batch_size=args.eval_batch_size,tolerance=args.tolerance)
    test=evaluate(model,data['test'],batch_size=args.eval_batch_size,tolerance=args.tolerance)
    dev_gate=old._gate(dev,args.min_rate);test_gate=old._gate(test,args.min_rate)
    result={'schema':SCHEMA,'status':'PASS' if dev_gate['status']=='PASS' and
            test_gate['status']=='PASS' else 'FAIL','identity':ident,
            'best_step':best_step,'manifest':manifest,
            'dev':{'gate':dev_gate,'overall':dev['overall'],'by_tier':dev['by_tier']},
            'test':{'gate':test_gate,'overall':test['overall'],'by_tier':test['by_tier']},
            'uses_test_for_training_or_promotion':False,'oracle_XY_in_input':False,
            'gaussian_tv_not_comparable_to_signed_grid_tv':True,
            'numeric_depends_on_localized_bank_weights':True,
            'raw_token_comprehension_certified':False,
            'elapsed_seconds':round(time.monotonic()-started,3)}
    base._atomic_json(args.output/'summary.json',result)
    print(json.dumps({'status':result['status'],'best_step':best_step,'dev_gate':dev_gate,
                      'test_gate':test_gate,'summary':str(args.output/'summary.json')},indent=2),flush=True)
    return result


def _load_checkpoint(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise ValueError('GAUSSIAN_MAG_CUDA_UNAVAILABLE')
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    state=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    if state.get('schema')!=SCHEMA:raise ValueError('GAUSSIAN_MAG_CHECKPOINT_SCHEMA_MISMATCH')
    ident=state['identity'];model=_make_model(ident,args.device)
    model.load_state_dict(state['model']);model.eval()
    return model,ident,state


def audit(args):
    model,ident,state=_load_checkpoint(args)
    opts=argparse.Namespace(**{**ident,'device':args.device,'output':args.output,
              'eval_batch_size':args.eval_batch_size,'cpu_threads':args.cpu_threads})
    _validate(opts)
    data,domain,manifest=old._dataset(opts,torch.device(args.device))
    if ident != identity(opts,manifest,domain):raise ValueError('GAUSSIAN_MAG_AUDIT_IDENTITY_MISMATCH')
    selected={}
    for split in args.splits.split(','):
        if split in selected or split not in ('dev','test'):
            raise ValueError('GAUSSIAN_MAG_INVALID_SPLIT')
        e=evaluate(model,data[split],batch_size=args.eval_batch_size,tolerance=ident['tolerance'])
        selected[split]={'gate':old._gate(e,ident['min_rate']),
                         'overall':e['overall'],'by_tier':e['by_tier']}
    if not selected:raise ValueError('GAUSSIAN_MAG_EMPTY_SPLIT')
    report={'schema':SCHEMA+'.audit','checkpoint':str(args.checkpoint),'step':state['step'],
            'matrix_hashes':manifest['matrix_hashes'],'splits':selected,
            'status':'PASS' if all(s['gate']['status']=='PASS' for s in selected.values()) else 'FAIL'}
    if args.output:base._atomic_json(args.output,report)
    for split,s in selected.items():
        print(f'{split.upper()} gaussian-magnitude gate: {s["gate"]["status"]}')
        for tier,rec in s['gate']['tiers'].items():
            print(f'  {tier}: {rec["joint_all_masks_instance_rate"]:.1%} '
                  f'({rec["independent_matrix_count_cases"]} cases)')
    return report


def predict(args):
    model,ident,state=_load_checkpoint(args)
    prompt=args.prompt_file.read_text(encoding='utf8')
    visible=old.parse_blind(prompt,args.tier,args.mask)
    feature=old.features_from_prompt(prompt,args.tier,args.mask,ident['payoff_bound'])
    f=torch.tensor([feature],dtype=torch.float32,device=args.device)
    with torch.no_grad():
        out=model(f,with_density=False)
    xy=out['numeric'][0].cpu().tolist()
    answer={'schema':SCHEMA+'.prediction','step':state['step'],
            'predicted_X':xy[0],'predicted_Y':xy[1],
            'predicted_Z':'HAWK' if xy[0]>xy[1] else 'DOVE' if xy[0]<xy[1] else 'TIE',
            'visible_count_a':visible['a'],'visible_count_b':visible['b'],
            'visible_payoff_bindings':dict(zip(old.hd.TAGS,visible['payoffs'])),
            'oracle_XY_in_input':False,'oracle_labels_read':False}
    print(json.dumps(answer,indent=2))
    return answer



def diagnose(args):
    """Read-only case-level precision/sign analysis of a frozen Gaussian checkpoint.

    TEST is explicitly opt-in: examining it means it is no longer a fresh holdout
    for later tuning, regardless of the lack of gradient updates here.
    """
    model,ident,state=_load_checkpoint(args)
    opts=argparse.Namespace(**{**ident,'device':args.device,'output':args.output,
              'eval_batch_size':args.eval_batch_size,'cpu_threads':args.cpu_threads})
    _validate(opts)
    data,domain,manifest=old._dataset(opts,torch.device(args.device))
    if ident!=identity(opts,manifest,domain):raise ValueError('GAUSSIAN_MAG_DIAG_IDENTITY_MISMATCH')
    splits=args.splits.split(',')
    if not splits or len(splits)!=len(set(splits)) or any(s not in ('dev','test') for s in splits):
        raise ValueError('GAUSSIAN_MAG_DIAG_SPLIT_NOT_ALLOWED')
    root=args.output
    if root.exists() and any(root.iterdir()):raise FileExistsError('GAUSSIAN_MAG_DIAG_OUTPUT_EXISTS')
    root.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA+'.diagnosis.v1','checkpoint_step':state['step'],
            'checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
            'geometry':{'banks':ident['banks'],'min_mag':ident['min_mag'],
                        'power':ident['density_power'],'zero':'categorical',
                        'decode':'continuous location to localized Gaussian bank means'},
            'splits':{},'oracle_labels_used_only_for_read_only_scoring':True}
    names=['split','tier','instance','mask','matrix_index','a','b','HH','HD','DH','DD',
           'oracle_X','oracle_Y','pred_X','pred_Y','raw_magnitude_X','raw_magnitude_Y',
           'pred_sign_X','pred_sign_Y','oracle_sign_X','oracle_sign_Y',
           'abs_error_X','abs_error_Y','relative_error_X','relative_error_Y',
           'pass_X','pass_Y','pass_both','min_abs_oracle','magnitude_bucket',
           'continuous_location_X','continuous_location_Y','top_bank_X','top_bank_Y','top_center_X','top_center_Y',
           'top_weight_X','top_weight_Y','effective_banks_X','effective_banks_Y']
    for split in splits:
        rows=[];source=data[split]
        with torch.no_grad():
            for start in range(0,len(source['rows']),args.eval_batch_size):
                out=model(source['features'][start:start+args.eval_batch_size],with_density=False)
                vals=out['numeric'].cpu().tolist();mags=out['magnitude'].cpu().tolist()
                classes=out['sign_class'].cpu().tolist()
                locations=out['signed_location'].cpu().tolist()
                bank_w=out['weights'].cpu()
                best_ix=bank_w.argmax(-1)
                dominant=bank_w.gather(-1,best_ix.unsqueeze(-1)).squeeze(-1)
                effective=torch.exp(-(bank_w*bank_w.clamp_min(1e-30).log()).sum(-1))
                mean_centers=model.bank_means.cpu()
                for j,record in enumerate(source['rows'][start:start+args.eval_batch_size]):
                    visible=old.parse_blind(record['prompt'],record['tier'],record['mask'])
                    x,y=float(record['x']),float(record['y']);px,py=vals[j]
                    if (visible['a']*visible['payoffs'][0]+visible['b']*visible['payoffs'][1]!=x or
                        visible['a']*visible['payoffs'][2]+visible['b']*visible['payoffs'][3]!=y):
                        raise ValueError('GAUSSIAN_MAG_DIAG_ORACLE_INCONSISTENT_WITH_PROMPT')
                    ex,ey=abs(px-x),abs(py-y)
                    hx=ex<=ident['tolerance']*abs(x)
                    hy=ey<=ident['tolerance']*abs(y)
                    v=min(abs(x),abs(y))
                    bucket=('zero' if v==0 else '<=1' if v<=1 else '<=10' if v<=10 else
                            '<=100' if v<=100 else '<=1000' if v<=1000 else
                            '<=10000' if v<=10000 else '<=100000' if v<=100000 else '>100000')
                    rows.append(dict(split=split,tier=record['tier'],instance=record['instance'],
                       mask=record['mask'],matrix_index=record['matrix_index'],
                       a=visible['a'],b=visible['b'],
                       **dict(zip(('HH','HD','DH','DD'),visible['payoffs'])),
                       oracle_X=x,oracle_Y=y,pred_X=px,pred_Y=py,
                       raw_magnitude_X=mags[j][0],raw_magnitude_Y=mags[j][1],
                       pred_sign_X=classes[j][0]-1,pred_sign_Y=classes[j][1]-1,
                       oracle_sign_X=(x>0)-(x<0),oracle_sign_Y=(y>0)-(y<0),
                       abs_error_X=ex,abs_error_Y=ey,
                       relative_error_X=ex/abs(x) if x else None,
                       relative_error_Y=ey/abs(y) if y else None,
                       pass_X=hx,pass_Y=hy,pass_both=hx and hy,
                       min_abs_oracle=v,magnitude_bucket=bucket,
                       continuous_location_X=locations[j][0],continuous_location_Y=locations[j][1],
                       top_bank_X=int(best_ix[j,0]),top_bank_Y=int(best_ix[j,1]),
                       top_center_X=float(mean_centers[best_ix[j,0]]),
                       top_center_Y=float(mean_centers[best_ix[j,1]]),
                       top_weight_X=float(dominant[j,0]),top_weight_Y=float(dominant[j,1]),
                       effective_banks_X=float(effective[j,0]),
                       effective_banks_Y=float(effective[j,1])))
        path=root/f'{split}_cases.csv'
        with path.open('w',newline='',encoding='utf-8') as fd:
            writer=csv.DictWriter(fd,fieldnames=names);writer.writeheader();writer.writerows(rows)
        groups=defaultdict(list)
        for item in rows:groups[(item['tier'],item['instance'])].append(item)
        def rates(parts):
            n=len(parts)
            return {'cases':n,'X_rate':sum(all(x['pass_X'] for x in g) for g in parts)/n if n else None,
                    'Y_rate':sum(all(x['pass_Y'] for x in g) for g in parts)/n if n else None,
                    'joint_rate':sum(all(x['pass_both'] for x in g) for g in parts)/n if n else None,
                    'sign_both_rate':sum(all(x['pred_sign_X']==x['oracle_sign_X'] and
                                            x['pred_sign_Y']==x['oracle_sign_Y'] for x in g) for g in parts)/n if n else None}
        by_tier={tier:rates([g for (t,_),g in groups.items() if t==tier]) for tier in old.TIERS}
        by_magnitude={bucket:rates([g for g in groups.values() if g[0]['magnitude_bucket']==bucket])
                      for bucket in ('zero','<=1','<=10','<=100','<=1000','<=10000','<=100000','>100000')}
        zero_cases=[g for g in groups.values() if g[0]['oracle_X']==0 or g[0]['oracle_Y']==0]
        report['splits'][split]={'by_tier':by_tier,'by_min_oracle_magnitude':by_magnitude,
              'exact_zero_cases':rates(zero_cases),
              'overall_tier_cases':rates(list(groups.values())),'case_csv':str(path)}
        print(f'{split.upper()} gaussian-magnitude diagnosis:')
        for tier,score in by_tier.items():
            print(f'  {tier}: joint={score["joint_rate"]:.1%}, sign_both={score["sign_both_rate"]:.1%}')
        for name,score in by_magnitude.items():
            if score['cases']:
                print(f'  magnitude {name}: joint={score["joint_rate"]:.1%} cases={score["cases"]}')
    base._atomic_json(root/'diagnosis.json',report)
    print('DIAGNOSIS_JSON='+str(root/'diagnosis.json'),flush=True)
    return report


def inspect_banks(args):
    max_mag=1.25*old.TOTAL*args.payoff_bound
    centers,logs,sigmas,basis,edges=magnitude_geometry(
        args.banks,args.grid_bins,args.min_mag,max_mag,args.density_power)
    intervals=[(0,.001),(.001,.01),(.01,.1),(.1,1),(1,2),(2,10),
               (10,100),(100,1000),(1000,10000),(10000,100000),(100000,max_mag)]
    shells=[]
    for a,b in intervals:
        count=int(((centers>=a)&(centers<b if b!=max_mag else centers<=b)).sum())
        shells.append({'from':a,'to':b,'banks':count,
                       'banks_per_unit':count/(b-a)})
    # Ideal neighboring-bank interpolation is an upper bound on REPRESENTATION,
    # not neural prediction. Never use this oracle interpolation in inference.
    reference=[]
    for number in (0,1e-5,.001,.01,.1,1,1.5,2,10,1000,1000000,max_mag):
        if number==0:
            decoded=0.
        else:
            ix=int(torch.searchsorted(centers,torch.tensor(number)).clamp(1,len(centers)-1))
            a,b=float(centers[ix-1]),float(centers[ix])
            frac=(number-a)/(b-a) if b>a else 0
            decoded=(1-frac)*a+frac*b
        reference.append({'oracle_magnitude':number,'ideal_decoded_magnitude':decoded,
                          'relative_error':abs(decoded-number)/number if number else 0.})
    report={'schema':SCHEMA+'.geometry.v1','banks':args.banks,'grid_bins':args.grid_bins,
            'min_magnitude':args.min_mag,'max_magnitude':max_mag,
            'density_power':args.density_power,'shell_allocation':shells,
            'gaussian_width':{'relative_sigma':DEFAULT_GAUSSIAN_CV,
                         'min_sigma':float(sigmas[0]),'max_sigma':float(sigmas[-1])},
            'reference_interpolation_only_not_learned':reference,
            'gaussian_bank_mass_max_error':float((basis.sum(-1)-1).abs().max())}
    if args.output:base._atomic_json(args.output,report)
    print(json.dumps(report,indent=2))
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    def options(a):
        a.add_argument('--banks',type=int,default=2000)
        a.add_argument('--grid-bins',type=int,default=512)
        a.add_argument('--min-mag',type=float,default=DEFAULT_MIN_MAGNITUDE)
        a.add_argument('--density-power',type=float,default=DEFAULT_DENSITY_POWER)
        a.add_argument('--payoff-bound',type=int,default=4)
    t=sub.add_parser('train',help='Train with small-dense Gaussian magnitude banks and certify')
    options(t)
    t.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    t.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    t.add_argument('--hidden',type=int,default=128)
    t.add_argument('--steps',type=int,default=15000)
    t.add_argument('--probe-every',type=int,default=500)
    t.add_argument('--batch-size',type=int,default=64)
    t.add_argument('--eval-batch-size',type=int,default=256)
    t.add_argument('--cpu-threads',type=int,default=4)
    t.add_argument('--lr',type=float,default=.003)
    t.add_argument('--min-lr',type=float,default=.00003)
    t.add_argument('--seed',type=int,default=20261022)
    t.add_argument('--train-matrices',type=int,default=96)
    t.add_argument('--dev-matrices',type=int,default=24)
    t.add_argument('--test-matrices',type=int,default=24)
    t.add_argument('--counts-per-matrix',type=int,default=16)
    t.add_argument('--masks',type=int,choices=(1,2),default=2)
    t.add_argument('--tolerance',type=float,default=.05)
    t.add_argument('--min-rate',type=float,default=.8)
    a=sub.add_parser('audit',help='Read-only certification of frozen Gaussian magnitude checkpoint')
    a.add_argument('--checkpoint',type=Path,required=True)
    a.add_argument('--output',type=Path)
    a.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    a.add_argument('--splits',default='dev,test')
    a.add_argument('--eval-batch-size',type=int,default=256)
    a.add_argument('--cpu-threads',type=int,default=4)
    q=sub.add_parser('predict',help='Standalone inference from an oracle-blind prompt')
    q.add_argument('--checkpoint',type=Path,required=True)
    q.add_argument('--prompt-file',type=Path,required=True)
    q.add_argument('--tier',choices=old.TIERS,required=True)
    q.add_argument('--mask',type=int,choices=(0,1),default=0)
    q.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    q.add_argument('--cpu-threads',type=int,default=4)
    d=sub.add_parser('diagnose',help='Read-only sign/zero/magnitude failure analysis')
    d.add_argument('--checkpoint',type=Path,required=True)
    d.add_argument('--output',type=Path,required=True)
    d.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    d.add_argument('--splits',default='dev')
    d.add_argument('--eval-batch-size',type=int,default=256)
    d.add_argument('--cpu-threads',type=int,default=4)
    g=sub.add_parser('inspect-banks',help='Inspect unsigned bank allocation and Gaussian widths')
    options(g)
    g.add_argument('--output',type=Path)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    return {'train':train,'audit':audit,'predict':predict,
            'inspect-banks':inspect_banks,'diagnose':diagnose}[args.command](args)

if __name__=='__main__':main()
