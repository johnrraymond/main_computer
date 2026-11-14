#!/usr/bin/env python3
"""Independent HybridAgent teacher-representation experiments 1 and 2.

1) Reconstruct four visible count*payoff products from frozen and trainable
   typed embeddings, compared with exact fixed-vector and raw-product controls.
2) Train a causal-attention Gaussian X/Y predictor using ONLY four teacher
   product tokens (no pretrained numeric anchor), with fixed, frozen-v4, and
   jointly trainable typed encodings.

Uses the exact source/checkpoint/split verification of the read-only v4 probe.
Source checkpoints and prior HybridAgent runs are never modified.  TRAIN is
used for gradient updates, DEV for selection, optional TEST once after freeze.
This is teacher-forced diagnosis, NOT a self-generating HybridAgent.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F
from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as blind
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss
from tools import nanojev_hawk_dove_gaussian_hybrid_agent_v4 as v4
from tools import nanojev_hawk_dove_hybrid_teacher_probe as probe

SCHEMA='nanojev.hawk-dove.hybrid-representation-lab.v1'
ARMS=('recon_frozen','recon_joint','attention_fixed','attention_frozen','attention_joint')


def fixed_tokens(features,dim):
    """Lossless within float32 precision: role one-hot, exact product at index 4.

    This is a diagnostic upper/control input embedding, not a learned token.
    Four teacher products are derived only from the six visible operands.
    """
    if dim<7:raise ValueError('REP_LAB_EMBEDDING_DIM_TOO_SMALL')
    products=v4.stage_values(features)
    out=products.new_zeros(len(features),4,dim)
    idx=torch.arange(4,device=features.device)
    out[:,idx,idx]=1.
    out[:,:,4]=products
    out[:,:,5]=torch.sign(products)
    out[:,:,6]=torch.log1p(products.abs())
    return out


class ProductDecoder(nn.Module):
    def __init__(self, hidden, typed, joint):
        super().__init__()
        self.typed=typed
        self.joint=joint
        if not joint:
            for p in self.typed.parameters():p.requires_grad_(False)
        # Separate per-role readouts allow a fair inverse to the typed embeddings.
        self.readout=nn.Sequential(nn.LayerNorm(hidden),nn.Linear(hidden,hidden),
                                   nn.GELU(),nn.Linear(hidden,1))

    def forward(self, features):
        return self.readout(self.typed(features)).squeeze(-1)

    def train(self,mode=True):
        super().train(mode)
        if not self.joint:self.typed.eval()
        return self


class TeacherAttention(nn.Module):
    """Independent causal reasoning + Gaussian output; NO Gaussian anchor input."""
    def __init__(self,reference,typed,mode,heads=4,layers=1):
        super().__init__()
        self.mode=mode
        self.typed=typed
        if mode=='frozen':
            for p in self.typed.parameters():p.requires_grad_(False)
        h=reference.hidden
        self.core=v4.CausalMemoryCore(h,heads,layers,max_tokens=4)
        self.readout=nn.Sequential(nn.LayerNorm(h),nn.Linear(h,h),nn.GELU(),nn.Linear(h,2))
        nn.init.zeros_(self.readout[-1].weight)
        nn.init.zeros_(self.readout[-1].bias)
        # Immutable pretrained Gaussian numerical density geometry.
        for attr in ('centers','basis','bank_log_gains','log_proposal_sigma'):
            self.register_buffer(attr,getattr(reference,attr).detach().clone())
        self.bins=reference.bins;self.banks=reference.banks

    def train(self,mode=True):
        super().train(mode)
        if self.mode=='frozen':self.typed.eval()
        return self

    def tokens(self,features):
        return fixed_tokens(features,self.core.positions.shape[1]) if self.mode=='fixed' else self.typed(features)

    def forward(self,features,return_location=False):
        return self.forward_tokens(self.tokens(features),return_location=return_location)

    def forward_tokens(self,tokens,return_location=False):
        hidden=self.core(tokens)  # Only product embeddings; no _context, no anchor.
        location=self.readout(hidden).sigmoid()
        density=self.density_from_location(location)
        return (density,location) if return_location else density

    def density_from_location(self,location):
        sigma=self.log_proposal_sigma.exp().clamp(min=.3,max=20.)
        delta=(self.centers.reshape(1,1,-1)-location.unsqueeze(-1))*self.bins
        logits=-.5*(delta/sigma.reshape(1,2,1)).square()+self.bank_log_gains
        masses=F.softmax(logits,dim=-1)
        density=(masses.reshape(-1,self.banks)@self.basis).reshape(-1,2,self.bins)
        return density


def product_xy(products,scale):
    return torch.stack((products[:,0]+products[:,1],
                        products[:,2]+products[:,3]),dim=1)*scale


def strict_metrics(prediction,split,tolerance=.05):
    truth=split['numeric'].detach().cpu()
    pred=prediction.detach().cpu()
    if pred.shape!=truth.shape:raise ValueError('REP_LAB_SHAPE_MISMATCH')
    hits=((pred-truth).abs() <= tolerance*truth.abs()).all(dim=1).tolist()
    grouped={}
    for row,hit in zip(split['rows'],hits):
        key=(row['tier'],row['instance'])
        grouped.setdefault(key,[]).append(bool(hit))
    by_tier={t:sum(all(values) for (tier,_),values in grouped.items() if tier==t)/
               sum(tier==t for tier,_ in grouped) for t in blind.TIERS}
    err=(pred-truth).abs()
    zero=(truth==0)
    return {'min_tier_joint5':min(by_tier.values()),'per_tier':by_tier,
            'mean_abs_xy_error':float(err.mean()),'max_abs_xy_error':float(err.max()),
            'exact_zero_coordinates':int(zero.sum()),
            'exact_zero_hits':int(((err==0)&zero).sum()),
            'presentations':int(len(pred)),'independent_instances':int(len(grouped))}


@torch.no_grad()
def evaluate_reconstruction(model,split,scale,batch_size,tolerance):
    model.eval()
    output=[];prod_err=[]
    for x in split['features'].split(batch_size):
        pred=model(x)
        expected=v4.stage_values(x)
        output.append(product_xy(pred,scale).cpu())
        prod_err.append((pred-expected).abs().cpu())
    err=torch.cat(prod_err)
    return {**strict_metrics(torch.cat(output),split,tolerance),
            'mean_abs_normalized_product_error':float(err.mean()),
            'max_abs_normalized_product_error':float(err.max())}


@torch.no_grad()
def evaluate_attention(model,split,domain,batch_size,tolerance):
    model.eval()
    outputs=[]
    for x in split['features'].split(batch_size):
        outputs.append(gauss.numeric_decode(model(x),domain).cpu())
    return strict_metrics(torch.cat(outputs),split,tolerance)


@torch.no_grad()
def attention_ablation(model,split,domain,batch_size,tolerance):
    """True distinct-instance shuffle; model sees NO original numerical context."""
    model.eval()
    full=[]
    for chunk in split['features'].split(batch_size):
        full.append(model.tokens(chunk).detach())
    tokens=torch.cat(full)
    donors=v4.distinct_instance_thought_donors(split['features'],tokens,split['rows'])
    shuffled=tokens.index_select(0,donors)
    if bool((shuffled==tokens).flatten(1).all(dim=1).any()):
        raise RuntimeError('REP_LAB_ABLATION_SHUFFLE_UNCHANGED')
    predictions={}
    for name,inputs in [('correct',tokens),('shuffled',shuffled),('zero_tokens',torch.zeros_like(tokens))]:
        preds=[]
        for chunk in inputs.split(batch_size):
            preds.append(gauss.numeric_decode(model.forward_tokens(chunk),domain).cpu())
        predictions[name]=torch.cat(preds)
    diagnostics={k:strict_metrics(v,split,tolerance) for k,v in predictions.items()}
    truth=split['numeric'].detach().cpu()
    hits={name:((pred-truth).abs()<=tolerance*truth.abs()).all(dim=1).tolist()
          for name,pred in predictions.items()}
    grouped={}
    for i,row in enumerate(split['rows']):
        key=(row['tier'],row['instance'])
        grouped.setdefault(key,{k:[] for k in predictions})
        for label in predictions:grouped[key][label].append(bool(hits[label][i]))
    paired={}
    for control in ('shuffled','zero_tokens'):
        rescued=sum(all(v['correct']) and not all(v[control]) for v in grouped.values())
        regressed=sum(all(v[control]) and not all(v['correct']) for v in grouped.values())
        paired[control]={'rescued':rescued,'regressed':regressed,'net':rescued-regressed}
    return {'accuracy':diagnostics,'paired_case_differences':paired,
            'all_shuffled_sequences_distinct':True,'masks_share_donors':True}


@torch.no_grad()
def oracle_density_ceiling(reference,dev,domain,batch_size,tolerance):
    """The ceiling imposed by Gaussian basis/256-bin numerical decoding."""
    shell=TeacherAttention(reference,nn.Identity(),'fixed')
    shell=shell.to(dev['features'].device).eval()
    predicted=[]
    for truth in dev['numeric'].split(batch_size):
        location=torch.stack([(truth[:,j]-domain[j][0])/(domain[j][1]-domain[j][0])
                              for j in range(2)],dim=1).clamp(0.,1.)
        predicted.append(gauss.numeric_decode(shell.density_from_location(location),domain).cpu())
    return strict_metrics(torch.cat(predicted),dev,tolerance)


@torch.no_grad()
def controls(model,splits,scale,tolerance):
    result={}
    for name in ('train','dev'):
        data=splits[name]
        products=v4.stage_values(data['features'])
        result[name]={
            'raw_products':strict_metrics(product_xy(products,scale),data,tolerance),
            'fixed_tokens_exact_inverse':strict_metrics(
                product_xy(fixed_tokens(data['features'],model.reference.hidden)[:,:,4],scale),
                data,tolerance),
        }
        if result[name]['raw_products']!=result[name]['fixed_tokens_exact_inverse']:
            raise AssertionError('REP_LAB_FIXED_TOKEN_INFORMATION_LOSS')
    return result


def make_arms(model,heads,layers):
    hidden=model.reference.hidden
    def typed():return copy.deepcopy(model.typed)
    arms={
        'recon_frozen':ProductDecoder(hidden,typed(),False),
        'recon_joint':ProductDecoder(hidden,typed(),True),
        'attention_fixed':TeacherAttention(model.reference,nn.Identity(),'fixed',heads,layers),
        'attention_frozen':TeacherAttention(model.reference,typed(),'frozen',heads,layers),
        'attention_joint':TeacherAttention(model.reference,typed(),'joint',heads,layers),
    }
    # Parameter-matched controls must start with IDENTICAL decoder/core weights.
    arms['recon_joint'].readout.load_state_dict(arms['recon_frozen'].readout.state_dict())
    for key in ('attention_frozen','attention_joint'):
        arms[key].core.load_state_dict(arms['attention_fixed'].core.state_dict())
        arms[key].readout.load_state_dict(arms['attention_fixed'].readout.state_dict())
    return arms


def _num_loss(density,truth,domain,bins,sigma,location):
    # Matched Gaussian density objective and direct normalized numerical location.
    target=gauss.normalized_target(truth,domain,bins,sigma)
    kl=(target*(target.clamp_min(1e-12).log()-density.clamp_min(1e-12).log())).sum(-1).mean()
    den=torch.tensor([d[1]-d[0] for d in domain],device=truth.device,dtype=truth.dtype)
    normalized=torch.stack([(truth[:,i]-domain[i][0])/den[i] for i in range(2)],dim=1)
    return kl + 10*F.mse_loss(location,normalized) + .02*(density-target).square().mean()*bins


def make_identity(source,run_dir,config,source_ci,args):
    keys=('steps_reconstruction','steps_attention','batch_size','eval_batch_size',
          'probe_every','lr','seed','heads','layers','tolerance','cpu_threads')
    return {'schema':SCHEMA,'source_sha256':probe.sha256(source),
            'teacher_sha256':probe.sha256(run_dir/args.checkpoint),
            'teacher_schema':config['schema'],
            'source_identity':source_ci,'matrix_hashes':config['matrix_hashes'],
            'options':{key:getattr(args,key) for key in keys},
            'train_only_supervision':True,'DEV_selection':True,
            'TEST_final_only':args.final_test,
            'input_policy':'four visible count-payoff products; no anchor or oracle answer at inference'}


def _save(path,arm,optimizer,generator,step,best_score,identity,history):
    gauss._atomic_save(path,{'schema':SCHEMA+'.progress','identity':identity,'arm':arm.__class__.__name__,
           'step':step,'model':arm.state_dict(),'optimizer':optimizer.state_dict(),
           'sampler_rng':generator.get_state(), 'torch_rng':torch.get_rng_state(),
           'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
           'best_score':best_score,'history':history})


def _load(path,arm,optimizer,generator,identity):
    record=torch.load(path,map_location='cpu',weights_only=True)
    if record.get('schema')!=SCHEMA+'.progress' or record.get('identity')!=identity or record.get('arm')!=arm.__class__.__name__:
        raise RuntimeError('REP_LAB_RESUME_IDENTITY_DRIFT')
    arm.load_state_dict(record['model'],strict=True)
    optimizer.load_state_dict(record['optimizer'])
    generator.set_state(record['sampler_rng'])
    torch.set_rng_state(record['torch_rng'])
    if record['cuda_rng'] is not None and torch.cuda.is_available():torch.cuda.set_rng_state_all(record['cuda_rng'])
    return record


def train_arm(key,arm,split,domain,scale,args,out,identity,device):
    recon=key.startswith('recon_')
    steps=args.steps_reconstruction if recon else args.steps_attention
    arm_dir=out/key
    if arm_dir.exists() and (arm_dir/'done.json').exists():
        return json.loads((arm_dir/'done.json').read_text(encoding='utf-8'))
    arm_dir.mkdir(parents=True,exist_ok=True)
    arm=arm.to(device)
    params=[p for p in arm.parameters() if p.requires_grad]
    if not params:raise RuntimeError('REP_LAB_NO_TRAINABLE_PARAMETERS')
    optimizer=torch.optim.AdamW(params,lr=args.lr,weight_decay=0.)
    generator=torch.Generator(device='cpu').manual_seed(args.seed+37+ARMS.index(key)*1009)
    progress=arm_dir/'latest.pt';best_file=arm_dir/'best.pt'
    history=[];best_score=None;start=0
    if args.resume:
        if progress.exists():
            record=_load(progress,arm,optimizer,generator,identity)
            history=record['history'];best_score=record['best_score'];start=record['step']
        elif any(arm_dir.iterdir()):
            raise RuntimeError('REP_LAB_INCOMPLETE_ARM_WITHOUT_PROGRESS')
    elif any(arm_dir.iterdir()):
        raise FileExistsError('REP_LAB_REFUSES_OVERWRITE:'+str(arm_dir))
    source=split['train']; n=len(source['rows'])
    def eval_dev():
        return (evaluate_reconstruction(arm,split['dev'],scale,args.eval_batch_size,args.tolerance)
                if recon else evaluate_attention(arm,split['dev'],domain,args.eval_batch_size,args.tolerance))
    stop=min(steps,args.stop_after or steps)
    for step in range(start+1,stop+1):
        arm.train()
        ids=torch.randint(n,(args.batch_size,),generator=generator).to(device)
        x=source['features'].index_select(0,ids)
        optimizer.zero_grad(set_to_none=True)
        if recon:
            value=arm(x)
            loss=F.mse_loss(value,v4.stage_values(x))
        else:
            truth=source['numeric'].index_select(0,ids)
            density,loc=arm(x,return_location=True)
            loss=_num_loss(density,truth,domain,arm.bins,identity['source_identity']['target_sigma_bins'],loc)
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError('REP_LAB_NONFINITE_LOSS')
        loss.backward()
        nn.utils.clip_grad_norm_(params,1.,error_if_nonfinite=True)
        optimizer.step()
        if step%args.probe_every==0 or step==steps:
            dev=eval_dev()
            # Strict joint-within-5% ranks first; MAE breaks ties.
            rank=(dev['min_tier_joint5'],-dev['mean_abs_xy_error'])
            if best_score is None or rank>tuple(best_score):
                best_score=list(rank)
                gauss._atomic_save(best_file,{'schema':SCHEMA+'.best','identity':identity,
                         'key':key,'step':step,'model':arm.state_dict(),'DEV':dev})
            event={'step':step,'loss':float(loss.detach()),'DEV':dev,
                   'best_min_joint5':best_score[0]}
            history.append(event)
            print(f'REP_LAB_TRAIN arm={key} step={step}/{steps} '
                  f'DEV_min_joint5={dev["min_tier_joint5"]:.4f} '
                  f'DEV_xy_MAE={dev["mean_abs_xy_error"]:.6g}',flush=True)
            _save(progress,arm,optimizer,generator,step,best_score,identity,history)
    if stop<steps:
        return {'status':'PAUSED','key':key,'step':stop,'planned':steps}
    # Selection is frozen before optional TEST evaluation.
    best=torch.load(best_file,map_location='cpu',weights_only=True)
    if best.get('schema')!=SCHEMA+'.best' or best.get('identity')!=identity or best.get('key')!=key:
        raise RuntimeError('REP_LAB_BEST_CHECKPOINT_DRIFT')
    arm.load_state_dict(best['model'],strict=True)
    ablation=(attention_ablation(arm,split['dev'],domain,args.eval_batch_size,args.tolerance)
              if not recon else None)
    report={'schema':SCHEMA,'arm':key,'best_step':best['step'],
            'DEV_thought_ablation':ablation,
            'DEV':best['DEV'],'train_updates':steps,
            'TEST':(evaluate_reconstruction(arm,split['test'],scale,args.eval_batch_size,args.tolerance)
                    if recon else evaluate_attention(arm,split['test'],domain,args.eval_batch_size,args.tolerance))
                    if args.final_test else None,
            'history':history}
    gauss._atomic_json(arm_dir/'done.json',report)
    return report


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('REP_LAB_CUDA_UNAVAILABLE')
    if (min(args.steps_reconstruction,args.steps_attention,args.batch_size,args.eval_batch_size,
            args.probe_every,args.cpu_threads,args.heads,args.layers)<1 or not 0<args.lr<=1 or
            not 0<args.tolerance<1):
        raise ValueError('REP_LAB_INVALID_ARGUMENTS')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    source=Path(args.reference_checkpoint).expanduser().resolve()
    run_dir=Path(args.run_dir).expanduser().resolve()
    src_before=probe.sha256(source);teacher_before=probe.sha256(run_dir/args.checkpoint)
    model,splits,domain,config,_=probe.load_experiment(source,run_dir,args.device,args.checkpoint)
    ci=config['source_identity']
    if ci['hidden']%args.heads:raise ValueError('REP_LAB_HEAD_DIM_NOT_DIVISIBLE')
    identity=make_identity(source,run_dir,config,ci,args)
    out=Path(args.output).expanduser().resolve()
    if not args.resume:
        if out.exists() and any(out.iterdir()):raise FileExistsError('REP_LAB_REFUSES_OVERWRITE')
        out.mkdir(parents=True,exist_ok=True)
        gauss._atomic_json(out/'config.json',identity)
    elif not (out/'config.json').exists() or json.loads((out/'config.json').read_text(encoding='utf-8'))!=identity:
        raise RuntimeError('REP_LAB_RESUME_IDENTITY_DRIFT')
    scale=blind.TOTAL*ci['payoff_bound']
    oracle=controls(model,splits,scale,args.tolerance)
    oracle['gaussian_true_location_DEV_ceiling']=oracle_density_ceiling(
        model.reference,splits['dev'],domain,args.eval_batch_size,args.tolerance)
    oracle['pretrained_typed_linear_probe']=probe.linear_encoding_readout(
        model,splits['train'],splits['dev'],ci['payoff_bound'],args.eval_batch_size,
        tolerance=args.tolerance)
    gauss._atomic_json(out/'controls.json',oracle)
    print('REP_LAB_SOURCE_VERIFIED=TRUE IMMUTABLE_CHECKPOINTS=TRUE '
          'NO_GAUSSIAN_ANCHOR_IN_THOUGHT_ONLY=TRUE',flush=True)
    print('REP_LAB_RAW_PRODUCTS_DEV='+json.dumps(oracle['dev']['raw_products'],separators=(',',':')),flush=True)
    print('REP_LAB_GAUSSIAN_ORACLE_CEILING='+json.dumps(oracle['gaussian_true_location_DEV_ceiling']['min_tier_joint5']),flush=True)
    torch.manual_seed(args.seed)
    arms=make_arms(model,args.heads,args.layers)
    reports={}
    for key in ARMS:
        result=train_arm(key,arms[key],splits,domain,scale,args,out,identity,torch.device(args.device))
        if result.get('status')=='PAUSED':
            print('REP_LAB_PAUSED='+json.dumps(result),flush=True)
            return result
        reports[key]=result
    after=(probe.sha256(source),probe.sha256(run_dir/args.checkpoint))
    if (src_before,teacher_before)!=after:
        raise RuntimeError('REP_LAB_INPUT_CHECKPOINT_MUTATED')
    summary={'schema':SCHEMA,'identity':identity,'controls':oracle,
             'arms':{k:{'best_step':r['best_step'],'DEV':r['DEV'],'TEST':r['TEST'],
                         'DEV_thought_ablation':r['DEV_thought_ablation']} for k,r in reports.items()},
             'interpretation':{
                 'frozen_embedding_product_reconstruction':reports['recon_frozen']['DEV']['mean_abs_normalized_product_error'],
                 'trained_embedding_product_reconstruction':reports['recon_joint']['DEV']['mean_abs_normalized_product_error'],
                 'frozen_embedding_attention_joint5':reports['attention_frozen']['DEV']['min_tier_joint5'],
                 'joint_embedding_attention_joint5':reports['attention_joint']['DEV']['min_tier_joint5'],
                 'lossless_embedding_attention_joint5':reports['attention_fixed']['DEV']['min_tier_joint5'],
                 'all_attention_arms_use_only_teacher_products':True,
                 'prior_gaussian_shortcut':False,
                 'oracle_answer_supplied_as_input':False,
                 'historical_holdouts_inspected_before':True,
                 'no_self_generated_thoughts_in_this_experiment':True}}
    gauss._atomic_json(out/'report.json',summary)
    print('REP_LAB_RESULT='+json.dumps({k:round(r['DEV']['min_tier_joint5'],6) for k,r in reports.items()}),flush=True)
    print('REP_LAB_REPORT='+str(out/'report.json'),flush=True)
    return summary


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--checkpoint',choices=('teacher_best.pt',),default='teacher_best.pt')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps-reconstruction',type=int,default=2000)
    p.add_argument('--steps-attention',type=int,default=6000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261023)
    p.add_argument('--heads',type=int,default=4)
    p.add_argument('--layers',type=int,default=1)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--stop-after',type=int,default=None,help='Optional pause at this step in the current arm')
    p.add_argument('--final-test',action='store_true',help='Evaluate previous TEST split once after DEV selection')
    return p


def main(argv=None):return run(parser().parse_args(argv))

if __name__=='__main__':main()
