#!/usr/bin/env python3
"""Sixteen fixed-anchor Gaussian banks after the frozen original two-bank head.

The first NEW bank has fixed signed anchor centers at +/-1,000,000 (half of
2,000,000). Each next bank halves the magnitude of its anchors, reaching
+/-30.517578125 at bank sixteen. Within a bank, fixed Gaussian registers span
[-2*anchor, +2*anchor], with overlapping support between neighboring banks.
These register locations NEVER depend on an earlier bank's output.

A learned mixture selects a proposal on each fixed grid and learns how to
blend it with the previous estimate. Every new bank starts as exact identity.
Banks train and freeze one at a time. The frozen Stage 2 model, original
snapshots and untouched test split retain the legacy validation protections.

IMPORTANT: the frozen source fixture upstream still exposes Y. These heads
never consume Y or true X in their forward paths.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from pathlib import Path

import torch
from torch import nn

from tools import nanojev_hawk_dove_coarse_to_fine_gaussian as fine
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_correction_ab as store
from tools import nanojev_hawk_dove_internal_curriculum_master as master

SCHEMA='nanojev.hawk-dove.gaussian-fixed-ladder-16.v1'
STAGES=tuple(range(3,19))
BASE_STAGE=2
BANK_COUNT=len(STAGES)
RELATIVE_MAX=2_000_000.
DEFAULT_PARENT=fine.DEFAULT_OUTPUT/'best_checkpoints'/'best_small_gaussian.pt'
DEFAULT_OUTPUT=Path.home()/'NanoJev/runs/hawk_dove_gaussian_fixed_ladder_16_v1'
TIERS=fine.TIERS


def digest(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def safe_bytes(path):
    return master.checked_file(path)


def check_parent(args):
    """Load the second-bank Gaussian without treating its external path as mutable authority."""
    root=args.refinement_checkpoint.parent.parent if args.refinement_checkpoint.parent.name=='best_checkpoints' else args.refinement_checkpoint.parent
    read=safe_bytes(args.refinement_checkpoint)
    meta=args.refinement_checkpoint.with_suffix('.json')
    if not meta.is_file():raise ValueError('STACK_MISSING_REFINEMENT_METADATA')
    md=json.loads(meta.read_text(encoding='utf8'))
    parent=master.load_state(read)
    if (parent.get('schema')!=fine.SCHEMA or md.get('checkpoint_sha256')!=digest(read) or
        md.get('arm')!='gaussian' or int(md.get('step',-1))!=int(parent.get('step',-2))):
        raise ValueError('STACK_REFINEMENT_PROVENANCE_MISMATCH')
    ident=parent['identity']
    frozen_parent=safe_bytes(root/'parent_snapshot.pt')
    donor=safe_bytes(root/'donor_snapshot.pt')
    if (digest(frozen_parent)!=ident['parent_sha256'] or digest(donor)!=ident['donor_sha256'] or
        fork.fingerprint(args.source)!=ident['source_sha256'] or
        fork.fingerprint(args.teacher)!=ident['teacher_sha256']):
        raise ValueError('STACK_FROZEN_SOURCE_MISMATCH')
    if args.seed!=int(ident['seed']) or args.dev_per_tier!=int(ident['dev_per_tier']):
        raise ValueError('STACK_PARENT_SPLIT_MISMATCH')
    if int(ident['train_per_tier'])!=args.train_per_tier:
        raise ValueError('STACK_PARENT_TRAIN_SPLIT_MISMATCH')
    return read,frozen_parent,donor,md


def check_args(args):
    if (args.additional_steps<0 or args.steps_per_bank<1 or args.chunk_steps<1 or
        args.views<9 or args.train_per_tier<8 or args.dev_per_tier<8 or args.test_per_tier<8 or
        args.batch_size<1 or args.relative_max<=0 or
        args.relative_weight<0 or args.small_boost<=0 or args.lr<=0 or
        args.small_threshold<=0 or not 0<args.gate_percent<=100 or
        not all(map(math.isfinite,(args.relative_max,args.lr,args.relative_weight,args.small_boost,
                                    args.small_threshold,args.gate_percent)))):
        raise ValueError('STACK_INVALID_ARGUMENTS')
    if args.additional_steps>len(STAGES)*args.steps_per_bank:
        raise ValueError('STACK_TOO_MANY_ADDITIONAL_STEPS')
    fine.fault.verify_no_overlap(args.output,[args.refinement_checkpoint.parent,
             args.refinement_checkpoint.parent.parent,args.source,args.teacher])
    return args.output.expanduser().resolve()


def identity(args,first_sha,original_sha,donor_sha,first_id):
    return {'schema':SCHEMA,'source_refinement_path':str(args.refinement_checkpoint.expanduser().resolve()),
            'stage2_sha256':first_sha,'original_parent_sha256':original_sha,
            'donor_sha256':donor_sha,'source_sha256':first_id['source_sha256'],
            'teacher_sha256':first_id['teacher_sha256'],
            'stage2_step':int(first_id.get('step',0)),
            'stage2_head_arm':'gaussian','original_bridge_arm':first_id['parent_arm'],
            'relative_max':args.relative_max,'anchor_ratio':0.5,'bank_count':BANK_COUNT,
            'geometry':'fixed_absolute_symmetric_gaussian_grids_not_predecessor_centered',
            'steps_per_bank':args.steps_per_bank,'views':args.views,
            'train_per_tier':args.train_per_tier,'dev_per_tier':args.dev_per_tier,
            'test_per_tier':args.test_per_tier,'batch_size':args.batch_size,
            'gate_percent':args.gate_percent,'small_threshold':args.small_threshold,
            'small_boost':args.small_boost,'relative_weight':args.relative_weight,
            'lr':args.lr,'seed':args.seed,
            'training':'one fixed-anchor Gaussian bank at a time, freeze preceding banks',
            'benchmark_visibility':'upstream 011 exposes Y; no result injected into the new heads'}


def load_or_prepare(args):
    out=check_args(args)
    names=('stage2_snapshot.pt','parent_snapshot.pt','donor_snapshot.pt','config.json','latest.pt')
    exist=[(out/n).exists() for n in names]
    if any(exist) and not all(exist):raise ValueError('STACK_PARTIAL_RUN_STATE')
    resume=all(exist)
    if resume:
        first=safe_bytes(out/'stage2_snapshot.pt')
        original=safe_bytes(out/'parent_snapshot.pt')
        donor=safe_bytes(out/'donor_snapshot.pt')
        previous=json.loads((out/'config.json').read_text(encoding='utf8'))
        step=int(master.load_state(first)['step'])
        first_id={**master.load_state(first)['identity'],'step':step}
        ident=identity(args,digest(first),digest(original),digest(donor),first_id)
        if ident!=previous or (digest(first)!=previous['stage2_sha256'] or
           digest(original)!=previous['original_parent_sha256'] or
           digest(donor)!=previous['donor_sha256'] or
           fork.fingerprint(args.source)!=ident['source_sha256'] or
           fork.fingerprint(args.teacher)!=ident['teacher_sha256']):
            raise ValueError('STACK_RESUME_DRIFT')
        saved=master.load_state(safe_bytes(out/'latest.pt'))
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('STACK_BAD_RESUME_STATE')
        md=None
    else:
        first,original,donor,md=check_parent(args)
        step=int(master.load_state(first)['step'])
        first_id={**master.load_state(first)['identity'],'step':step}
        ident=identity(args,digest(first),digest(original),digest(donor),first_id)
        saved=None
    return out,ident,saved,(first,original,donor),md


def scalar_features(features,origin,current,previous,info):
    """Use only predictions and predicted Gaussian occupancy, never target values."""
    prob=info['probabilities']
    if prob is None:raise ValueError('STACK_NON_GAUSSIAN_PREDECESSOR')
    radius=info['radius'].detach().clamp_min(1.)
    offsets=torch.stack((
        (current-origin)/(origin.abs()+125000.),
        (current-previous)/radius,
        torch.log10(1.+current.abs())/math.log10(1.+fine.fork.MAX_ABS),
        torch.sign(current),
        -((prob*prob.clamp_min(1e-10).log()).sum(-1))/math.log(prob.shape[-1]),
        (prob*torch.linspace(-1.,1.,prob.shape[-1],device=prob.device)[None,:]).sum(-1)),dim=-1)
    return torch.cat((features.float(),prob.float()*prob.shape[-1],offsets.float()),dim=-1)


def fixed_anchor(stage, relative_max=RELATIVE_MAX):
    """Absolute anchor magnitudes, independent of predictions or training."""
    if stage not in STAGES or not math.isfinite(relative_max) or relative_max<=0:
        raise ValueError('LADDER_INVALID_ANCHOR')
    return relative_max*2.**(-(stage-2))


class FixedAnchorBank(fine.LocalRefiner):
    """Learn a proposal on a fixed absolute Gaussian register lattice.

    The two Gaussian anchor locations are -anchor and +anchor. Register
    support [-2*anchor,+2*anchor] overlaps the adjacent bank's support.
    Unlike the old residual model, no register location is translated by
    the prior prediction. The prior estimate is only the blending baseline.
    """
    def __init__(self, feature_width, *, anchor, registers=129, sigma_bins=2.,temperature=8.):
        if not math.isfinite(anchor) or anchor<=0:raise ValueError('LADDER_BAD_ANCHOR')
        super().__init__(feature_width,kind='gaussian',registers=registers,
                         sigma_bins=sigma_bins,floor=2*anchor,fraction=1.,temperature=temperature)
        self.anchor=float(anchor)
        self.sigma=self.anchor/2.
        locations=self.centers*(2*self.anchor)
        positive=torch.exp(-.5*((locations-self.anchor)/self.sigma).square())
        negative=torch.exp(-.5*((locations+self.anchor)/self.sigma).square())
        # The actual Gaussian lobes are centered at +/- anchor, with sigma
        # = anchor/2. Adjacent halves retain substantial tail overlap.
        self.register_buffer('anchor_envelope',positive+negative)
        self.mix=nn.Linear(feature_width,1)
        nn.init.zeros_(self.mix.weight)
        nn.init.zeros_(self.mix.bias)

    def forward(self, features, previous):
        if features.ndim!=2 or previous.ndim!=1 or features.shape[0]!=len(previous):
            raise ValueError('LADDER_INPUT_SHAPE')
        logits=self.readout(features.float())
        weights=torch.softmax(logits*self.temperature,dim=-1)
        posterior=(weights@self.gaussian_kernel)*self.anchor_envelope
        posterior=posterior/posterior.sum(-1,keepdim=True).clamp_min(1e-12)
        # Fixed absolute positions. The tensor is never centered on previous.
        absolute_centers=self.centers*(2*self.anchor)
        proposed=posterior@absolute_centers
        blend=torch.tanh(self.mix(features.float()).squeeze(-1))
        result=previous+blend*(proposed-previous)
        radius=torch.full_like(previous,2*self.anchor)
        return result,{'radius':radius,'probabilities':posterior,'logits':logits,
                       'offset':(result-previous)/radius,'proposal':proposed,
                       'blend':blend,'anchor':self.anchor}


class Cascade(nn.Module):
    def __init__(self,base_width,stage2,*,relative_max=RELATIVE_MAX):
        super().__init__()
        if not math.isfinite(relative_max) or relative_max<=0:
            raise ValueError('LADDER_BAD_RELATIVE_MAX')
        self.stage2=stage2
        self.stage2.eval().requires_grad_(False)
        self.relative_max=float(relative_max)
        prior_width=base_width+stage2.registers+6
        self.banks=nn.ModuleDict({str(k):FixedAnchorBank(
            prior_width,anchor=fixed_anchor(k,relative_max),registers=stage2.registers,
            sigma_bins=2.,temperature=stage2.temperature) for k in STAGES})

    def upto(self,data,last=None,return_input=False,with_details=False):
        """Return Stage 2 and every later fixed-grid proposal/blended prediction."""
        if last is None:last=STAGES[-1]
        if last<BASE_STAGE or last>STAGES[-1]:raise ValueError('LADDER_INVALID_DEPTH')
        base=data['coarse'];feat=data['features']
        current,info=self.stage2(feat,base)
        predecessor=base
        predictions={2:current}
        details={2:info}
        for k in STAGES:
            if k>last:break
            inp=scalar_features(feat,base,current,predecessor,info)
            if return_input and k==last:
                return {'features':inp,'coarse':current,'target':data['target']}
            next_value,next_info=self.banks[str(k)](inp,current)
            predecessor,current,info=current,next_value,next_info
            predictions[k]=current
            details[k]=info
        return (predictions,details) if with_details else predictions

    def active_inputs(self,data,k):
        return self.upto(data,k,True)


def objective(head, data, small_boost, small_threshold, relative_weight, relative_max):
    """Use one global training scale, not the shrinking per-bank anchor.

    In the moving-residual model, errors were divided by the moving radius.
    That would artificially magnify training gradients by 2^15 for the last
    fixed bank, overwhelming small-X examples with errors at other scales.
    Every fixed bank instead sees the same global normalization, together
    with the existing small-X relative-error objective.
    """
    target=data['target']
    pred,_=head(data['features'],data['coarse'])
    global_error=(pred-target)/relative_max
    absolute=torch.nn.functional.smooth_l1_loss(global_error,
              torch.zeros_like(global_error),beta=.05,reduction='none')
    relative_error=(pred-target)/target.abs().clamp_min(20000.)
    relative=torch.nn.functional.smooth_l1_loss(relative_error,
              torch.zeros_like(relative_error),beta=.05,reduction='none')
    weights=torch.where(target.abs()<small_threshold,small_boost,1.)
    return ((absolute+relative_weight*relative)*weights).sum()/weights.sum()


def score_predictions(truth,pred,coarse,threshold,previous=None,radius=None):
    small=truth.abs()<threshold
    large=truth.abs()>=1000000.
    correct=(pred-truth).abs()/truth.abs().clamp_min(1.)<=.05
    return {'cases':int(truth.numel()),'correct':int(correct.sum()),
            'accuracy_5pct':float(correct.float().mean()),
            'small_n':int(small.sum()),'small_correct':int(correct[small].sum()),
            'small_mae':float((pred[small]-truth[small]).abs().mean()) if small.any() else None,
            'mean_absolute_error':float((pred-truth).abs().mean()),
            'large_n':int(large.sum()),'large_correct':int(correct[large].sum()),
            'mean_abs_change_from_stage2':float((pred-coarse).abs().mean()),
            'mean_abs_change_from_previous':float((pred-(previous if previous is not None else coarse)).abs().mean()),
            'local_coverage':float(((truth-(previous if previous is not None else coarse)).abs()<=radius).float().mean()) if radius is not None else None,
            'small_local_coverage':float(((truth[small]-(previous if previous is not None else coarse)[small]).abs()<=radius[small]).float().mean()) if radius is not None and small.any() else None}


@torch.no_grad()
def probe(cascade,dev,gate,threshold,step):
    cascade.eval()
    tiers_by_stage={k:{} for k in (2,*STAGES)}
    for tier in TIERS:
        data=dev[tier]
        predictions,details=cascade.upto(data,with_details=True)
        for k,pred in predictions.items():
            prev=predictions[k-1] if k>2 else data['coarse']
            score=score_predictions(data['target'],pred,predictions[2],threshold,
                                    prev,details[k]['radius'] if k==2 else None)
            if k>2:
                # Absolute geometric support is NOT a predecessor-relative
                # accuracy/coverage gate. Do not confuse them in reporting.
                radius=details[k]['radius'];truth=data['target'];small=truth.abs()<threshold
                score['fixed_support_coverage']=float((truth.abs()<=radius).float().mean())
                score['small_fixed_support_coverage']=(float((truth[small].abs()<=radius[small]).float().mean())
                                                        if bool(small.any()) else None)
                score['anchor']=details[k]['anchor']
            tiers_by_stage[k][tier]=score
    scores={}
    for stage,tiers in tiers_by_stage.items():
        correct=sum(tiers[t]['correct'] for t in TIERS)
        total=sum(tiers[t]['cases'] for t in TIERS)
        nsmall=sum(tiers[t]['small_n'] for t in TIERS)
        small=sum(tiers[t]['small_correct'] for t in TIERS)
        minimum=min(tiers[t]['accuracy_5pct'] for t in TIERS)
        failing=[t for t in TIERS if tiers[t]['accuracy_5pct']+1e-10<gate/100.]
        scores[str(stage)]={'scores':tiers,'macro_accuracy_5pct':correct/total,
          'small_x_correct':small,'small_x_n':nsmall,'small_x_accuracy_5pct':small/nsmall,
          'small_x_mae':sum(tiers[t]['small_mae']*tiers[t]['small_n'] for t in TIERS)/nsmall,
          'minimum_accuracy_5pct':minimum,'failing_tiers':failing,'all_nine_pass':not failing,
          'correct':correct,'cases':total,
           'small_local_coverage':(sum(tiers[t]['small_local_coverage']*tiers[t]['small_n'] for t in TIERS)/nsmall
                                  if stage==2 else None),
          'small_fixed_support_coverage':(sum(tiers[t]['small_fixed_support_coverage']*tiers[t]['small_n'] for t in TIERS)/nsmall
                                           if stage>2 else None),
          'fixed_support_coverage':(sum(tiers[t]['fixed_support_coverage']*tiers[t]['cases'] for t in TIERS)/total
                                    if stage>2 else None),
          'anchor':fixed_anchor(stage,cascade.relative_max) if stage>2 else None,
          'sigma':fixed_anchor(stage,cascade.relative_max)/2 if stage>2 else None}
    return {'schema':SCHEMA+'.probe.v1','step':step,
      'stage_being_trained':None,
      'stages':scores,'small_threshold':threshold,'gate_percent':gate}


def active_stage(step,steps_per_bank):
    if step<1 or step>steps_per_bank*len(STAGES):
        raise ValueError('STACK_STEP_OUTSIDE_BUDGET')
    return STAGES[(step-1)//steps_per_bank]


def snapshot(cascade,optimizers,ident,step,args):
    return {'schema':SCHEMA,'identity':ident,'step':step,
      'bank_states':{str(k):cascade.banks[str(k)].state_dict() for k in STAGES},
      'optimizers':{str(k):optimizers[str(k)].state_dict() for k in STAGES},
      'rng_cpu':torch.get_rng_state(),
      'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}


def rank(score,metric):
    if metric=='small':return (score['small_x_accuracy_5pct'],score['macro_accuracy_5pct'],score['minimum_accuracy_5pct'])
    if metric=='macro':return (score['macro_accuracy_5pct'],score['small_x_accuracy_5pct'],score['minimum_accuracy_5pct'])
    if metric=='all_nine':return (score['minimum_accuracy_5pct'],score['macro_accuracy_5pct'],score['small_x_accuracy_5pct'])
    raise ValueError('STACK_UNKNOWN_BEST_METRIC')


def retain_best(out,ck,report):
    raw=io.BytesIO();torch.save(ck,raw);contents=raw.getvalue()
    emitted=[]
    for metric in ('small','macro','all_nine'):
        for depth,score in report['stages'].items():
            # An untrained downstream identity bank is NOT a best checkpoint.
            # Stage k starts at (k-3)*steps_per_bank+1, after k-3 banks.
            if int(depth)>2 and ck['step']<((int(depth)-3)*ck['identity']['steps_per_bank']+1):
                continue
            if metric=='all_nine' and not score['all_nine_pass']:continue
            path=out/'best_checkpoints'/f'best_{metric}_stage{depth}.pt'
            metadata=path.with_suffix('.json')
            value=rank(score,metric)
            if path.exists()!=metadata.exists():raise ValueError('STACK_PARTIAL_BEST')
            if metadata.exists():
                old=json.loads(metadata.read_text(encoding='utf8'))
                if (old['stage2_sha256']!=ck['identity']['stage2_sha256'] or
                    digest(safe_bytes(path))!=old['checkpoint_sha256']):
                    raise ValueError('STACK_BEST_INTEGRITY_FAILED')
                if value<=tuple(old['rank']):continue
            store.atomic_bytes(path,contents)
            store.atom_json(metadata,{'schema':SCHEMA+'.best.v1','metric':metric,'stage':int(depth),
                'step':ck['step'],'rank':value,'stage2_sha256':ck['identity']['stage2_sha256'],
                'checkpoint_sha256':digest(contents)})
            emitted.append({'metric':metric,'stage':int(depth),'step':ck['step'],
                            'macro':score['macro_accuracy_5pct'],
                            'small':score['small_x_accuracy_5pct']})
    return emitted


def verify_stage2(info,md):
    if md is None:return
    metric=md['metric'];expected=tuple(float(z) for z in md['rank'])
    found=rank(info['stages']['2'],metric)
    if any(abs(a-b)>1e-6 for a,b in zip(found,expected)):
        raise ValueError(f'STACK_STAGE2_SCORE_MISMATCH:{found}:{expected}')


def load_features(args,model,decoder,bridge,cache,split):
    return fine.rows_for(model,decoder,bridge,cache,args,split)


def run(args):
    out,ident,saved,copies,md=load_or_prepare(args)
    first,original,donor=copies
    budget=args.steps_per_bank*len(STAGES)
    start=int(saved['step']) if saved else 0
    if not args.test_only and start+args.additional_steps>budget:raise ValueError('STACK_BUDGET_EXCEEDED')
    fork.emit('stack_prepare',step=start,stage_count=2+BANK_COUNT,new_banks=BANK_COUNT,
              frozen_stage2_step=ident['stage2_step'],stage2_sha256=ident['stage2_sha256'],
              original_immutable=True,test_split_accessed=args.test_only,
              upstream_y_visible=True)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    original_ck=master.load_state(original);donor_ck=master.load_state(donor)
    orig_args=argparse.Namespace(arm=ident['original_bridge_arm'])
    decoder,bridge=fine.init_frozen(orig_args,original_ck,donor_ck,model)
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',{
        'schema':SCHEMA+'.capture','source_sha256':ident['source_sha256'],
        'teacher_sha256':ident['teacher_sha256'],
        'max_prompt_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    if args.test_only:
        if saved is None:raise ValueError('STACK_TEST_REQUIRES_SAVED_RUN')
        best=out/'best_checkpoints'/f'best_{args.best_metric}_stage{args.best_stage}.pt'
        if not best.exists():raise ValueError('STACK_BEST_NOT_AVAILABLE')
        meta=json.loads(best.with_suffix('.json').read_text(encoding='utf8'))
        contents=safe_bytes(best)
        if (meta['checkpoint_sha256']!=digest(contents) or meta['stage']!=args.best_stage or
            meta['metric']!=args.best_metric):raise ValueError('STACK_TEST_INTEGRITY_FAILED')
        chosen=master.load_state(contents)
        if chosen['identity']!=ident:raise ValueError('STACK_TEST_IDENTITY_DRIFT')
        test,_=load_features(args,model,decoder,bridge,cache,'test')
        base_width=int(test[TIERS[0]]['features'].shape[-1])
        s2_id=master.load_state(first)['identity']
        stage2=fine.LocalRefiner(base_width,kind='gaussian',registers=int(s2_id['registers']),
          sigma_bins=float(s2_id['sigma_bins']),floor=float(s2_id['floor']),
          fraction=float(s2_id['fraction']),temperature=float(s2_id['temperature'])).to(model['device'])
        stage2.load_state_dict(master.load_state(first)['heads']['gaussian'],strict=True)
        cascade=Cascade(base_width,stage2,relative_max=args.relative_max).to(model['device'])
        for k in STAGES:cascade.banks[str(k)].load_state_dict(chosen['bank_states'][str(k)])
        result=probe(cascade,test,args.gate_percent,args.small_threshold,chosen['step'])
        result['schema']=SCHEMA+'.untouched-test.v1';result['best_stage']=args.best_stage
        result['best_metric']=args.best_metric;result['checkpoint_sha256']=digest(contents)
        store.atom_json(out/f'test-{args.best_metric}-stage{args.best_stage}-step-{chosen["step"]:07d}.json',result)
        fork.emit('stack_test_finished',stage=args.best_stage,step=chosen['step'],
                  macro=result['stages'][str(args.best_stage)]['macro_accuracy_5pct'],
                  small=result['stages'][str(args.best_stage)]['small_x_accuracy_5pct'])
        return result
    train,tr_meta=load_features(args,model,decoder,bridge,cache,'train')
    dev,dv_meta=load_features(args,model,decoder,bridge,cache,'dev')
    if {str(r['instance']) for t in TIERS for r in tr_meta[t]} & \
       {str(r['instance']) for t in TIERS for r in dv_meta[t]}:
        raise ValueError('STACK_TRAIN_DEV_INSTANCE_LEAKAGE')
    width=int(train[TIERS[0]]['features'].shape[-1])
    s2_id=master.load_state(first)['identity']
    stage2=fine.LocalRefiner(width,kind='gaussian',registers=int(s2_id['registers']),
          sigma_bins=float(s2_id['sigma_bins']),floor=float(s2_id['floor']),
          fraction=float(s2_id['fraction']),temperature=float(s2_id['temperature'])).to(model['device'])
    stage2.load_state_dict(master.load_state(first)['heads']['gaussian'],strict=True)
    cascade=Cascade(width,stage2,relative_max=args.relative_max).to(model['device'])
    optimizers={str(k):torch.optim.AdamW(cascade.banks[str(k)].parameters(),lr=args.lr) for k in STAGES}
    if saved:
        for k in STAGES:
            cascade.banks[str(k)].load_state_dict(saved['bank_states'][str(k)],strict=True)
            optimizers[str(k)].load_state_dict(saved['optimizers'][str(k)])
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:torch.cuda.set_rng_state_all(saved['rng_cuda'])
    else:
        initial=probe(cascade,dev,args.gate_percent,args.small_threshold,0)
        verify_stage2(initial,md)
        s2=initial['stages']['2']
        for k in STAGES:
            score=initial['stages'][str(k)]
            if score['correct']!=s2['correct'] or score['small_x_correct']!=s2['small_x_correct']:
                raise ValueError('STACK_NOT_INITIAL_IDENTITY')
        out.mkdir(parents=True,exist_ok=True)
        store.atomic_bytes(out/'stage2_snapshot.pt',first)
        store.atomic_bytes(out/'parent_snapshot.pt',original)
        store.atomic_bytes(out/'donor_snapshot.pt',donor)
        store.atom_json(out/'config.json',ident)
    def save(step):
        ck=snapshot(cascade,optimizers,ident,step,args)
        store.atom_torch(out/'latest.pt',ck)
        results=probe(cascade,dev,args.gate_percent,args.small_threshold,step)
        results['stage_being_trained']=active_stage(step,args.steps_per_bank) if step else None
        store.atom_json(out/f'probe-step-{step:07d}.json',results)
        for event in retain_best(out,ck,results):fork.emit('stack_best_saved',**event)
        summary={str(k):{'macro':results['stages'][str(k)]['macro_accuracy_5pct'],
                         'small':results['stages'][str(k)]['small_x_accuracy_5pct'],
                         'small_mae':results['stages'][str(k)]['small_x_mae'],
                         'all_nine':results['stages'][str(k)]['all_nine_pass']} for k in (2,*STAGES)}
        fork.emit('stack_probe',step=step,active_stage=results['stage_being_trained'],stages=summary)
        return results
    if saved is None:save(0)
    for step in range(start+1,start+args.additional_steps+1):
        stage=active_stage(step,args.steps_per_bank)
        batch=fine.take_sample(train,args.seed,step,args.views)
        cascade.train()
        cascade.stage2.eval()
        for k in STAGES:
            cascade.banks[str(k)].eval().requires_grad_(k==stage)
        with torch.no_grad():
            inp=cascade.active_inputs(batch,stage)
        target_head=cascade.banks[str(stage)]
        opt=optimizers[str(stage)];opt.zero_grad(set_to_none=True)
        loss=objective(target_head,inp,args.small_boost,args.small_threshold,
                       args.relative_weight,args.relative_max)
        if not bool(torch.isfinite(loss)):raise FloatingPointError('STACK_NONFINITE_LOSS:'+str(stage))
        loss.backward()
        torch.nn.utils.clip_grad_norm_(target_head.parameters(),1.)
        opt.step()
        if step%args.chunk_steps==0 or step==start+args.additional_steps or step%args.steps_per_bank==0:
            save(step)
    for p,sha in [('stage2_snapshot.pt','stage2_sha256'),('parent_snapshot.pt','original_parent_sha256'),('donor_snapshot.pt','donor_sha256')]:
        if digest(safe_bytes(out/p))!=ident[sha]:raise ValueError('STACK_IMMUTABLE_SNAPSHOT_DRIFT')
    fork.emit('stack_finished',step=start+args.additional_steps,latest=str(out/'latest.pt'),
              test_split_read=False)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refinement-checkpoint',type=Path,default=DEFAULT_PARENT)
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--additional-steps',type=int,default=48000)
    p.add_argument('--steps-per-bank',type=int,default=3000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--relative-max',type=float,default=RELATIVE_MAX)
    p.add_argument('--views',type=int,default=9)
    p.add_argument('--train-per-tier',type=int,default=256)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--test-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--gate-percent',type=float,default=40.)
    p.add_argument('--small-threshold',type=float,default=250000.)
    p.add_argument('--small-boost',type=float,default=2.)
    p.add_argument('--relative-weight',type=float,default=.2)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--test-only',action='store_true')
    p.add_argument('--best-stage',type=int,choices=(2,*STAGES),default=STAGES[-1])
    p.add_argument('--best-metric',choices=('small','macro','all_nine'),default='small')
    return p


def main(argv=None):run(parser().parse_args(argv))

if __name__=='__main__':main()
