#!/usr/bin/env python3
"""Learned second Gaussian bank, LOCAL to the frozen head's own coarse X prediction.

The first trained Gaussian decoder and continuous prebody bridge are frozen.
The second bank takes the first prediction plus its first-bank distribution and
internal features, and learns a fine signed *offset* in coordinates centred at
that prediction. No ground-truth X or Y is read by the forward path. Target X
enters only the supervised loss and evaluation. The current experiment retains
the upstream hidden-X/visible-Y 011 benchmark; this prototype does NOT yet
establish inference without Y present in that original benchmark.

Control: parameter-count-matched continuous local offset with the SAME inputs,
sampling, loss, initial zero correction, optimizer, and permitted radius.
Neither control nor treatment modifies the original checkpoint or Qwen.
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

from tools import nanojev_hawk_dove_gaussian_internal_bank_ab as internal
from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_correction_ab as store
from tools import nanojev_hawk_dove_numerical_fault_localize as fault
from tools import nanojev_hawk_dove_internal_curriculum_master as master

SCHEMA='nanojev.hawk-dove.coarse-to-fine-gaussian.v1'
TIERS=internal.TIERS
ARMS=('gaussian','continuous')
DEFAULT_PARENT=internal.DEFAULT_OUTPUT/'best_checkpoints'/'best_macro_continuous.pt'
DEFAULT_OUTPUT=Path.home()/'NanoJev/runs/hawk_dove_coarse_to_fine_gaussian_v1'


def local_radius(coarse, floor, fraction):
    if floor<=0 or fraction<=0:raise ValueError('FINE_INVALID_LOCAL_RADIUS')
    return torch.maximum(coarse.detach().abs()*fraction,
                         torch.full_like(coarse,floor))


class LocalRefiner(nn.Module):
    """128+1 ordered signed local Gaussian coordinates or matched smooth control.

    `coarse` is produced by a FROZEN first Gaussian head, never ground truth.
    The residual distribution is expressed in [-1,1] TIMES a per-case radius,
    enabling much finer absolute output resolution near small coarse values.
    The same operation is slot-generic; it does not depend on a Y-value input.
    """
    def __init__(self, feature_width, *, kind, registers=129, sigma_bins=2.,
                 floor=125000.,fraction=.5, temperature=8.):
        super().__init__()
        if kind not in ARMS or feature_width<1 or registers<9 or registers%2==0:
            raise ValueError('FINE_INVALID_HEAD')
        if min(sigma_bins,floor,fraction,temperature)<=0 or not all(map(math.isfinite,(sigma_bins,floor,fraction,temperature))):
            raise ValueError('FINE_INVALID_HEAD_SCALE')
        self.kind=kind
        self.floor=float(floor);self.fraction=float(fraction)
        self.temperature=float(temperature)
        self.registers=registers
        self.readout=nn.Linear(feature_width,registers)
        nn.init.zeros_(self.readout.weight);nn.init.zeros_(self.readout.bias)
        centers=torch.linspace(-1.,1.,registers,dtype=torch.float32)
        self.register_buffer('centers',centers)
        spacing=float((centers[1]-centers[0]).item())
        delta=(centers[:,None]-centers[None,:])/(spacing*sigma_bins)
        kernel=F.softmax(-.5*delta.square(),dim=-1)
        self.register_buffer('gaussian_kernel',kernel)
        self.register_buffer('uniform',torch.full((registers,),1./registers,dtype=torch.float32))

    def forward(self,features,coarse):
        if features.ndim!=2 or coarse.ndim!=1 or features.shape[0]!=len(coarse):
            raise ValueError('FINE_INPUT_SHAPE')
        logits=self.readout(features.float())
        if self.kind=='gaussian':
            weights=F.softmax(logits*self.temperature,dim=-1)
            # Center uniform occupancy explicitly; the zero initialization is
            # EXACTLY identity even near +/-5% scoring boundaries.
            state=(weights-self.uniform)@self.gaussian_kernel
            offset=state@self.centers
        else:
            weights=None
            # Exactly the same trainable parameters, fixed [-1,1] offset range.
            # Retain comparable zero-initialized gradient scale.
            offset=(torch.tanh(logits)*self.centers).sum(-1)/self.centers.abs().sum()
        radius=local_radius(coarse,self.floor,self.fraction)
        result=coarse+radius*offset
        return result,{'offset':offset,'radius':radius,'probabilities':weights,'logits':logits}


def verify_parent_baseline(baseline, score):
    """Verify exact dev successes, not backend-dependent float32 accuracy means.

    A parent checkpoint may save macro accuracy as a Python float while CUDA
    evaluation of this head uses torch.float32.mean(), producing values such as
    0.5416666832235124 instead of 234/432. Round the expected score only to a
    *whole number of correct cases*, reject scores not close to such a count,
    and require the captured baseline to have exactly that count. A one-case
    discrepancy still fails closed.
    """
    total=sum(int(baseline[t]['cases']) for t in TIERS)
    correct=sum(int(baseline[t]['correct']) for t in TIERS)
    expected=score.get('macro_accuracy_5pct',score.get('macro'))
    if expected is not None:
        expected=float(expected)
        count_value=expected*total
        # 1e-3 of an example allows float32 rounding, not a changed prediction.
        if (not math.isfinite(expected) or not 0.<=expected<=1. or
            abs(count_value-round(count_value))>1e-3 or
            correct!=round(count_value)):
            raise ValueError(f'FINE_PARENT_BASELINE_SCORE_MISMATCH:{correct}/{total}:{expected}')
    per_tier=score.get('tier_correct') or score.get('per_tier_correct')
    if per_tier is not None and any(baseline[t]['correct']!=int(per_tier[t]) for t in TIERS):
        raise ValueError('FINE_PARENT_BASELINE_TIER_MISMATCH')


def feature_from_parent(decoder,bridge,data):
    """Freeze first decoder and bridge, preserve captured baseline exactly."""
    with torch.no_grad():
        pre=data['features']
        changed,_=bridge(pre)
        first=internal.decode_frozen(decoder,changed)
        original=internal.decode_frozen(decoder,pre)
        coarse=data['original_x']+(first['xy'][:,0]-original['xy'][:,0])
        # This is the exact first bank's posterior, not a target-dependent hint.
        bank_p=F.softmax(first['bank_logits'][:,0,:].float(),dim=-1)
        g=first['log_magnitude'][:,0].float()
        spread=torch.sqrt(((decoder.centers.float()[None,:]-g[:,None]).square()*bank_p).sum(-1).clamp_min(0.))
        entropy=-(bank_p*bank_p.clamp_min(1e-12).log()).sum(-1)/math.log(bank_p.shape[-1])
        hidden=decoder.body(changed)[:,0,:].float()
        # Full first-bank occupancy and unchanged internal features; no x/y truth.
        information=torch.cat((hidden,bank_p*bank_p.shape[-1],
              (torch.log10(1+coarse.abs())/math.log10(1+fork.MAX_ABS)).unsqueeze(-1),
              torch.sign(coarse).unsqueeze(-1),
              spread.unsqueeze(-1),entropy.unsqueeze(-1)),dim=-1)
        if not bool(torch.isfinite(information).all()) or not bool(torch.isfinite(coarse).all()):
            raise ValueError('FINE_NONFINITE_COARSE_FEATURES')
        return {'features':information.detach(),'coarse':coarse.detach(),
                'target':data['target_x'].detach()}


def enriched(decoder,bridge,records):
    return {tier:feature_from_parent(decoder,bridge,data) for tier,data in records.items()}


def performance(head,data,small_threshold=250000.):
    with torch.no_grad():
        coarse=data['coarse'];truth=data['target']
        pred,details=head(data['features'],coarse)
        r=(pred-truth).abs()/truth.abs().clamp_min(1.)
        r0=(coarse-truth).abs()/truth.abs().clamp_min(1.)
        small=truth.abs()<small_threshold
        large=truth.abs()>=1_000_000.
        correct=r<=.05;coarse_ok=r0<=.05
        mask=lambda a,v: int(v[a].sum()) if bool(a.any()) else 0
        coverage=((truth-coarse).abs()<=details['radius'])
        return {'cases':int(truth.numel()),'correct':int(correct.sum()),
                'coarse_correct':int(coarse_ok.sum()),'accuracy_5pct':float(correct.float().mean()),
                'small_n':int(small.sum()),'small_correct':mask(small,correct),
                'coarse_small_correct':mask(small,coarse_ok),
                'large_n':int(large.sum()),'large_correct':mask(large,correct),
                'mean_absolute_error':float((pred-truth).abs().mean()),
                'small_mae':float((pred[small]-truth[small]).abs().mean()) if bool(small.any()) else None,
                'coarse_small_mae':float((coarse[small]-truth[small]).abs().mean()) if bool(small.any()) else None,
                'mean_abs_correction':float((pred-coarse).abs().mean()),
                'local_coverage':float(coverage.float().mean()),
                'small_local_coverage':float(coverage[small].float().mean()) if bool(small.any()) else None,
                'small_precision_given_coarse_localized':mask(small & (coarse.abs()<small_threshold),correct),
                'small_localized_n':int((small & (coarse.abs()<small_threshold)).sum())}


def report(arms,dev,gate,threshold,step):
    scores={}
    for name,head in arms.items():
        head.eval()
        tiers={t:performance(head,dev[t],threshold) for t in TIERS}
        macro=sum(tiers[t]['accuracy_5pct'] for t in TIERS)/len(TIERS)
        min_tier=min(tiers[t]['accuracy_5pct'] for t in TIERS)
        small_correct=sum(tiers[t]['small_correct'] for t in TIERS)
        small_n=sum(tiers[t]['small_n'] for t in TIERS)
        small_coarse=sum(tiers[t]['coarse_small_correct'] for t in TIERS)
        fails=[t for t in TIERS if tiers[t]['accuracy_5pct']+1e-10<gate/100]
        scores[name]={'scores':tiers,'macro_accuracy_5pct':macro,
               'minimum_accuracy_5pct':min_tier,'all_nine_pass':not fails,'failing_tiers':fails,
               'small_x_accuracy_5pct':small_correct/small_n,'small_x_correct':small_correct,
               'small_x_n':small_n,'coarse_small_x_correct':small_coarse,
               'small_x_mae':sum(tiers[t]['small_mae']*tiers[t]['small_n'] for t in TIERS)/small_n}
    return {'schema':SCHEMA+'.probe.v1','step':step,'gate_percent':gate,
            'small_threshold':threshold,'arms':scores}


def objective(head,data,small_boost=2.,small_threshold=250000.,relative_weight=.2):
    """Train ONLY the local error; targets never enter LocalRefiner.forward."""
    x=data['target'];coarse=data['coarse']
    predicted,latent=head(data['features'],coarse)
    radius=latent['radius']
    # Normalize by local range to teach local refinement independently of X size.
    normalized=(predicted-x)/radius
    per=F.smooth_l1_loss(normalized,torch.zeros_like(normalized),beta=.05,reduction='none')
    # Give small-X samples finer error pressure with no change to input path.
    rel=(predicted-x)/x.abs().clamp_min(20000.)
    relative=F.smooth_l1_loss(rel,torch.zeros_like(rel),beta=.05,reduction='none')
    weight=torch.where(x.abs()<small_threshold,small_boost,1.)
    loss=((per+relative_weight*relative)*weight).sum()/weight.sum()
    return loss


def take_sample(train,seed,step,views):
    selected=[]
    for j in range(views):
        tier=TIERS[(step*views+j)%len(TIERS)]
        rng=random.Random(f'fine/{seed}/{step}/{j}/{tier}')
        selected.append((tier,rng.randrange(len(train[tier]['target']))))
    return {k:torch.stack([train[t][k][i] for t,i in selected],dim=0)
            for k in ('features','coarse','target')}


def best_rank(arm,metric):
    if metric=='macro':return (arm['macro_accuracy_5pct'],arm['small_x_accuracy_5pct'],arm['minimum_accuracy_5pct'])
    if metric=='small':return (arm['small_x_accuracy_5pct'],arm['macro_accuracy_5pct'],arm['minimum_accuracy_5pct'])
    if metric=='all_nine':return (arm['minimum_accuracy_5pct'],arm['macro_accuracy_5pct'],arm['small_x_accuracy_5pct'])
    raise ValueError('FINE_BAD_BEST_METRIC')


def save_best(output,checkpoint,info):
    root=output/'best_checkpoints'
    buf=io.BytesIO();torch.save(checkpoint,buf);raw=buf.getvalue()
    results=[]
    for name in ARMS:
        arm=info['arms'][name]
        for metric in ('small','macro','all_nine'):
            if metric=='all_nine' and not arm['all_nine_pass']:continue
            pt=root/f'best_{metric}_{name}.pt';meta=pt.with_suffix('.json')
            rank=best_rank(arm,metric)
            if pt.exists()!=meta.exists():raise ValueError('FINE_PARTIAL_BEST:'+str(pt))
            if meta.exists():
                old=json.loads(meta.read_text(encoding='utf8'))
                if old['parent_sha256']!=checkpoint['identity']['parent_sha256'] or \
                   master.sha(master.checked_file(pt))!=old['checkpoint_sha256']:
                    raise ValueError('FINE_BEST_INTEGRITY_FAILED:'+name)
                if rank<=tuple(old['rank']):continue
            store.atomic_bytes(pt,raw)
            store.atom_json(meta,{'schema':SCHEMA+'.best.v1','arm':name,
              'metric':metric,'step':checkpoint['step'],'rank':rank,
              'parent_sha256':checkpoint['identity']['parent_sha256'],
              'checkpoint_sha256':hashlib.sha256(raw).hexdigest()})
            results.append({'arm':name,'metric':metric,'step':checkpoint['step'],
              'macro_accuracy_5pct':arm['macro_accuracy_5pct'],
              'small_x_accuracy_5pct':arm['small_x_accuracy_5pct']})
    return results


def validate(args):
    if (args.additional_steps<0 or args.chunk_steps<1 or args.views<9 or
        args.train_per_tier<8 or args.dev_per_tier<8 or args.test_per_tier<8 or
        args.batch_size<1 or args.registers<9 or args.registers%2==0 or
        not all(math.isfinite(x) for x in (args.lr,args.floor,args.fraction,args.temperature,
          args.sigma_bins,args.small_boost,args.small_threshold,args.relative_weight)) or
        min(args.lr,args.floor,args.fraction,args.temperature,args.sigma_bins,args.small_boost,args.small_threshold)<=0 or
        args.relative_weight<0 or not 0<args.gate_percent<=100):
        raise ValueError('FINE_INVALID_ARGUMENTS')
    return fault.verify_no_overlap(args.output,[args.checkpoint.parent,args.checkpoint.parent.parent,
                     args.source,args.teacher])


def initialize(args):
    out=validate(args)
    prior=out/'parent_snapshot.pt';donor_copy=out/'donor_snapshot.pt';config=out/'config.json';latest=out/'latest.pt'
    state=[p.exists() for p in (prior,donor_copy,config,latest)]
    if any(state) and not all(state):raise ValueError('FINE_PARTIAL_RUN_STATE')
    resume=all(state)
    if resume:
        parent_raw=master.checked_file(prior);donor_raw=master.checked_file(donor_copy)
        existing=json.loads(config.read_text(encoding='utf8'))
        parent=master.load_state(parent_raw);donor=master.load_state(donor_raw)
        # Resume is pinned to OUR immutable copy, even if a live best-checkpoint
        # watcher subsequently publishes a different external best checkpoint.
        if (hashlib.sha256(parent_raw).hexdigest()!=existing['parent_sha256'] or
            hashlib.sha256(donor_raw).hexdigest()!=existing['donor_sha256'] or
            fork.fingerprint(args.source)!=existing['source_sha256'] or
            fork.fingerprint(args.teacher)!=existing['teacher_sha256']):
            raise ValueError('FINE_RESUME_SOURCE_OR_SNAPSHOT_DRIFT')
        provenance={'checkpoint_sha256':existing['source_parent_sha256'],
                    'source_sha256':existing['source_sha256'],
                    'teacher_sha256':existing['teacher_sha256']}
    else:
        parent,donor,arm,provenance=fault.parent_bundle(args.checkpoint,args.source,args.teacher,args.arm)
        if arm!=args.arm:raise ValueError('FINE_ARM_MISMATCH')
        parent_raw=master.checked_file(args.checkpoint)
        donor_path=args.checkpoint.parent.parent/'donor_snapshot.pt' if args.checkpoint.parent.name=='best_checkpoints' else args.checkpoint.parent/'donor_snapshot.pt'
        donor_raw=master.checked_file(donor_path)
    parent_identity=parent['identity']['donor_identity'] if parent['schema']==master.SCHEMA else parent['identity']
    # Keep exactly the same held-out case selection when comparing the parent.
    if args.seed!=int(parent_identity['seed']):
        raise ValueError('FINE_PARENT_SEED_MISMATCH')
    ident={'schema':SCHEMA,'source_parent_sha256':provenance['checkpoint_sha256'],
       'parent_sha256':hashlib.sha256(parent_raw).hexdigest(),
       'donor_sha256':hashlib.sha256(donor_raw).hexdigest(),
       'source_sha256':provenance['source_sha256'],'teacher_sha256':provenance['teacher_sha256'],
       'checkpoint':str(args.checkpoint.expanduser().resolve()),'parent_arm':args.arm,
       'parent_step':int(parent['step']), 'registers':args.registers,'sigma_bins':args.sigma_bins,
       'floor':args.floor,'fraction':args.fraction,'temperature':args.temperature,
       'train_per_tier':args.train_per_tier,'dev_per_tier':args.dev_per_tier,
       'test_per_tier':args.test_per_tier,'batch_size':args.batch_size,
       'views':args.views,'lr':args.lr,'gate_percent':args.gate_percent,
       'small_boost':args.small_boost,'small_threshold':args.small_threshold,
       'relative_weight':args.relative_weight,'seed':args.seed,
       'architecture':'original frozen coarse X + signed local Gaussian correction; no truth in forward',
       'benchmark_visibility':'upstream 011 exposes Y; refinement has no explicit Y input',
       'arms':list(ARMS)}
    if resume:
        if existing!=ident:raise ValueError('FINE_RESUME_CONFIG_DRIFT')
        saved=master.load_state(master.checked_file(latest))
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('FINE_RESUME_STATE_DRIFT')
    else:saved=None
    return out,parent,donor,ident,saved,parent_raw,donor_raw


def init_frozen(args,parent,donor,model):
    ident=parent['identity']['donor_identity'] if parent['schema']==master.SCHEMA else parent['identity']
    model['decoder']=fork.build_for_resume(model,donor,ident['parent_registers'],ident['parent_sigma_steps'])
    decoder=model['decoder'];decoder.eval().requires_grad_(False)
    layer=decoder.body[0]
    width=int(layer.normalized_shape[0]) if hasattr(layer,'normalized_shape') else int(layer.in_features)
    kind='bank' if args.arm.startswith('bank') else 'continuous'
    bridge=internal.IntermediateBridge(width,int(ident['registers']),kind=kind,
             limit=float(ident['limit']),sigma_bins=float(ident['sigma_bins']),
             feedback_scale=float(ident['feedback_scale'])).to(model['device'])
    bridge.load_state_dict(parent['heads'][args.arm],strict=True)
    bridge.eval().requires_grad_(False)
    return decoder,bridge


def rows_for(model,decoder,bridge,cache,args,split):
    collection=internal.FrozenPreBody(decoder)
    sets={};rowmeta={}
    n=args.train_per_tier if split=='train' else args.dev_per_tier if split=='dev' else args.test_per_tier
    try:
        for tier in TIERS:
            rows=fork.science.rows_for_probe(args.teacher,split,tier,n,args.seed)
            rowmeta[tier]=rows
            captured=internal.capture_rows(model,collection,cache,rows,
                           args.batch_size if split=='train' else internal.DEV_BATCH,model['device'])
            sets[tier]=feature_from_parent(decoder,bridge,captured)
            fork.emit('fine_features_ready',tier=tier,split=split,rows=n)
    finally:collection.close()
    return sets,rowmeta


def make_checkpoint(arms,optimizers,ident,step,args):
    return {'schema':SCHEMA,'identity':ident,'step':step,
      'heads':{name:head.state_dict() for name,head in arms.items()},
      'optimizers':{name:opt.state_dict() for name,opt in optimizers.items()},
      'rng_cpu':torch.get_rng_state(),
      'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}


def run(args):
    out,parent,donor,ident,saved,parent_raw,donor_raw=initialize(args)
    fork.emit('fine_prepare',parent_step=ident['parent_step'],step=saved['step'] if saved else 0,
        parent_sha256=ident['parent_sha256'],immutable_parent=True,upstream_y_visible=True,
        frozen_backbone=True,refinement_training=not args.test_only and args.additional_steps>0,
        test_split_accessed=args.test_only)
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    decoder,bridge=init_frozen(args,parent,donor,model)
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',{
        'schema':SCHEMA+'.capture','source_sha256':ident['source_sha256'],
        'teacher_sha256':ident['teacher_sha256'],
        'max_prompt_tokens':model['config']['max_prompt_tokens']},args.cache_memory_mb)
    if args.test_only:
        if saved is None:raise ValueError('FINE_TEST_REQUIRES_TRAINED_RUN')
        path=out/'best_checkpoints'/f'best_{args.best_metric}_{args.best_arm}.pt'
        meta=json.loads(path.with_suffix('.json').read_text(encoding='utf8'))
        raw=master.checked_file(path)
        if meta['checkpoint_sha256']!=hashlib.sha256(raw).hexdigest() or meta['arm']!=args.best_arm:
            raise ValueError('FINE_TEST_BEST_INTEGRITY_FAILED')
        chosen=master.load_state(raw)
        if chosen['identity']!=ident:raise ValueError('FINE_TEST_IDENTITY_DRIFT')
        test,_=rows_for(model,decoder,bridge,cache,args,'test')
        n=test[TIERS[0]]['features'].shape[-1]
        heads={k:LocalRefiner(n,kind=k,registers=args.registers,sigma_bins=args.sigma_bins,
             floor=args.floor,fraction=args.fraction,temperature=args.temperature).to(model['device']) for k in ARMS}
        for k,h in heads.items():h.load_state_dict(chosen['heads'][k])
        result=report(heads,test,args.gate_percent,args.small_threshold,chosen['step'])
        result['schema']=SCHEMA+'.untouched-test.v1'
        result['best_metric']=args.best_metric;result['best_arm']=args.best_arm
        result['checkpoint_sha256']=meta['checkpoint_sha256']
        store.atom_json(out/f'test-{args.best_metric}-{args.best_arm}-step-{chosen["step"]:07d}.json',result)
        fork.emit('fine_test_finished',step=chosen['step'],arm=args.best_arm,
                  test_macro=result['arms'][args.best_arm]['macro_accuracy_5pct'],
                  test_small=result['arms'][args.best_arm]['small_x_accuracy_5pct'])
        return result
    train,trainmeta=rows_for(model,decoder,bridge,cache,args,'train')
    dev,devmeta=rows_for(model,decoder,bridge,cache,args,'dev')
    if {str(r['instance']) for tier in TIERS for r in trainmeta[tier]} & \
       {str(r['instance']) for tier in TIERS for r in devmeta[tier]}:
        raise ValueError('FINE_TRAIN_DEV_INSTANCE_LEAKAGE')
    width=train[TIERS[0]]['features'].shape[-1]
    arms={name:LocalRefiner(width,kind=name,registers=args.registers,sigma_bins=args.sigma_bins,
              floor=args.floor,fraction=args.fraction,temperature=args.temperature).to(model['device']) for name in ARMS}
    if sum(p.numel() for p in arms['gaussian'].parameters())!=sum(p.numel() for p in arms['continuous'].parameters()):
        raise ValueError('FINE_ARMS_NOT_PARAMETER_MATCHED')
    opts={name:torch.optim.AdamW(head.parameters(),lr=args.lr) for name,head in arms.items()}
    if saved is None and args.checkpoint.with_suffix('.json').exists():
        parent_id=parent['identity']['donor_identity'] if parent['schema']==master.SCHEMA else parent['identity']
        if args.dev_per_tier==int(parent_id['dev_per_tier']):
            meta=json.loads(args.checkpoint.with_suffix('.json').read_text(encoding='utf8'))
            score=meta.get('score') or meta.get('selection') or {}
            baseline={t:performance(arms['gaussian'],dev[t],args.small_threshold) for t in TIERS}
            verify_parent_baseline(baseline,score)
    if saved is not None:
        for name,head in arms.items():
            head.load_state_dict(saved['heads'][name],strict=True)
            opts[name].load_state_dict(saved['optimizers'][name])
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:torch.cuda.set_rng_state_all(saved['rng_cuda'])
    else:
        for tier in TIERS:
            for name,head in arms.items():
                baseline=performance(head,dev[tier],args.small_threshold)
                if baseline['correct']!=baseline['coarse_correct'] or baseline['mean_abs_correction']!=0.:
                    raise ValueError('FINE_INITIAL_IDENTITY_FAILED:'+tier+':'+name)
        out.mkdir(parents=True,exist_ok=True)
        store.atomic_bytes(out/'parent_snapshot.pt',parent_raw)
        store.atomic_bytes(out/'donor_snapshot.pt',donor_raw)
        store.atom_json(out/'config.json',ident)
    step=int(saved['step']) if saved else 0
    step=int(saved['step']) if saved else 0
    def save(s):
        ck=make_checkpoint(arms,opts,ident,s,args)
        store.atom_torch(out/'latest.pt',ck)
        info=report(arms,dev,args.gate_percent,args.small_threshold,s)
        store.atom_json(out/f'probe-step-{s:07d}.json',info)
        for event in save_best(out,ck,info):fork.emit('fine_best_saved',**event)
        a=info['arms']['gaussian'];b=info['arms']['continuous']
        fork.emit('fine_probe',step=s,baseline_macro=sum(v['coarse_correct']/v['cases'] for v in a['scores'].values())/len(TIERS),
                  baseline_small_correct=a['coarse_small_x_correct'],small_n=a['small_x_n'],
                  gaussian_macro=a['macro_accuracy_5pct'],gaussian_small=a['small_x_accuracy_5pct'],
                  continuous_macro=b['macro_accuracy_5pct'],continuous_small=b['small_x_accuracy_5pct'],
                  gaussian_failing=a['failing_tiers'],continuous_failing=b['failing_tiers'])
        return info
    if saved is None:save(0)
    for s in range(step+1,step+args.additional_steps+1):
        sample=take_sample(train,args.seed,s,args.views)
        for name,head in arms.items():
            head.train();opts[name].zero_grad(set_to_none=True)
            loss=objective(head,sample,args.small_boost,args.small_threshold,args.relative_weight)
            if not bool(torch.isfinite(loss)):raise FloatingPointError('FINE_NONFINITE_LOSS:'+name)
            loss.backward();torch.nn.utils.clip_grad_norm_(head.parameters(),1.)
            opts[name].step()
        if s%args.chunk_steps==0 or s==step+args.additional_steps:save(s)
    if (hashlib.sha256(master.checked_file(out/'parent_snapshot.pt')).hexdigest()!=ident['parent_sha256']
        or hashlib.sha256(master.checked_file(out/'donor_snapshot.pt')).hexdigest()!=ident['donor_sha256']):
        raise ValueError('FINE_FROZEN_SNAPSHOT_DRIFT')
    fork.emit('fine_finished',step=step+args.additional_steps,
              parent_unchanged=fork.fingerprint(args.checkpoint)==ident['parent_sha256'],
              test_split_read=False,results=str(out/f'probe-step-{step+args.additional_steps:07d}.json'))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,default=DEFAULT_PARENT)
    p.add_argument('--arm',default='continuous',choices=('continuous','bank',*master.ARMS))
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--additional-steps',type=int,default=3000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--views',type=int,default=9)
    p.add_argument('--train-per-tier',type=int,default=256)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--test-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--gate-percent',type=float,default=40.)
    p.add_argument('--registers',type=int,default=129)
    p.add_argument('--sigma-bins',type=float,default=2.)
    p.add_argument('--floor',type=float,default=125000.)
    p.add_argument('--fraction',type=float,default=.5)
    p.add_argument('--temperature',type=float,default=8.)
    p.add_argument('--small-threshold',type=float,default=250000.)
    p.add_argument('--small-boost',type=float,default=2.)
    p.add_argument('--relative-weight',type=float,default=.2)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--test-only',action='store_true')
    p.add_argument('--best-metric',choices=('small','macro','all_nine'),default='small')
    p.add_argument('--best-arm',choices=ARMS,default='gaussian')
    return p


def main(argv=None):run(parser().parse_args(argv))

if __name__=='__main__':main()
