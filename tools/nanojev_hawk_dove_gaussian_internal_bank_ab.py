#!/usr/bin/env python3
"""Frozen-donor internal numerical register A/B experiment.

Insert an additional trainable intermediate state BEFORE the trained Gaussian
reconstruction body; feed it back into that body, then retain the frozen
X/Y sign and 512-magnitude output registers. Compare a 129-place, Gaussian-
smoothed ordered register state against an equally parameterized continuous
hidden layer. Both arms receive identical rows and identical losses.

No Hawk-Dove formula is used for prediction. The live donor fork is never
modified. This is a controlled frozen-backbone representational probe, not
an assertion that an internal arithmetic algorithm has already been learned.
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

from tools import nanojev_hawk_dove_gaussian_register_fork as fork
from tools import nanojev_hawk_dove_gaussian_correction_ab as previous

SCHEMA = 'nanojev.hawk-dove.gaussian-internal-bank-ab.v1'
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_internal_bank_ab_v1'
TIERS = tuple(fork.hd.TIERS)
DEV_BATCH = previous.ORIGINAL_DEV_PROBE_BATCH_SIZE


class IntermediateBridge(nn.Module):
    """Exactly parameter-matched internal bridges; identity at initialization.

    Bank: a distribution over ordered signed coordinates, smoothed by a fixed
    Gaussian kernel. A learned feedback projection injects this state into
    PRE-BODY features. Control: 129 tanh hidden units and the same projection.
    Neither is a final-output correction or a numerical oracle.
    """
    def __init__(self, width: int, registers: int=129, *, kind: str,
                 limit: float=.4, sigma_bins: float=2., feedback_scale: float=.1):
        super().__init__()
        if kind not in ('bank', 'continuous') or width < 1 or registers < 9 or registers % 2 == 0:
            raise ValueError('INTERNAL_AB_BAD_ARCHITECTURE')
        if not 0 < limit or not 0 < sigma_bins or not 0 < feedback_scale <= 1:
            raise ValueError('INTERNAL_AB_BAD_BRIDGE_SCALE')
        self.kind = kind
        self.registers = registers
        self.feedback_scale = feedback_scale
        self.encode = nn.Linear(width, registers)
        self.feedback = nn.Linear(registers, width)
        # Every arm initially reproduces the frozen donor without approximation.
        nn.init.zeros_(self.feedback.weight)
        nn.init.zeros_(self.feedback.bias)
        centers=torch.linspace(-limit,limit,registers,dtype=torch.float32)
        self.register_buffer('centers',centers)
        sigma=float((centers[1]-centers[0]).item()) * sigma_bins
        offsets=(centers[:,None]-centers[None,:])/sigma
        kernel=F.softmax(-.5*offsets.square(),dim=-1)
        self.register_buffer('gaussian_kernel',kernel)

    def forward(self, prebody):
        logits=self.encode(prebody)
        if self.kind == 'bank':
            unsmoothed=F.softmax(logits,dim=-1)
            distribution=unsmoothed @ self.gaussian_kernel
            # Centered occupancy is dimensionally comparable to signed hidden
            # activations and removes the otherwise constant uniform component.
            state=self.registers*distribution-1.
            location=(distribution*self.centers).sum(dim=-1)
        else:
            distribution=None
            state=torch.tanh(logits)
            location=None
        feedback=self.feedback(state)*self.feedback_scale
        return prebody+feedback,{'feedback':feedback,'distribution':distribution,
                                  'location':location}


class FrozenPreBody:
    """Capture donor evidence just before decoder.body; no donor gradients."""
    def __init__(self, decoder):
        self.decoder=decoder
        self.decoder.eval().requires_grad_(False)
        self.last=None
        self.hook=decoder.body.register_forward_pre_hook(self._hook)

    def _hook(self, _mod, inputs):
        self.last=inputs[0].detach()

    def extract(self,model,prompts,channels,cache):
        self.last=None
        with torch.no_grad():
            original=fork.forward_masked(model,prompts,channels,cache)
        prebody=self.last
        if prebody is None or prebody.ndim != 3 or prebody.shape[0:2] != (len(prompts),2):
            raise ValueError('INTERNAL_AB_BODY_CAPTURE_MISSING')
        return {'features':prebody.float(),
                'original_x':original['xy'][:,0].detach().float(),
                'original_y':original['xy'][:,1].detach().float(),
                'original_g':original['log_magnitude'].detach().float()}

    def close(self):
        self.hook.remove()


def capture_rows(model,collector,cache,rows,batch_size,device):
    if not rows:raise ValueError('INTERNAL_AB_EMPTY_ROWS')
    pieces={k:[] for k in ('features','original_x','original_y','original_g','target_x','target_y')}
    for start in range(0,len(rows),batch_size):
        chunk=rows[start:start+batch_size]
        for row in chunk:
            if not fork.rt.token_eligible(model,row['prompt']):
                raise ValueError('INTERNAL_AB_INELIGIBLE_PROMPT:'+str(row['case_id']))
        channels=[fork.xyop.pack(r['x'],r['y'],'011') for r in chunk]
        observed=collector.extract(model,[r['prompt'] for r in chunk],channels,cache)
        observed['target_x']=torch.as_tensor([float(r['x']) for r in chunk],device=device)
        observed['target_y']=torch.as_tensor([float(r['y']) for r in chunk],device=device)
        for key in pieces:
            pieces[key].append(observed[key].detach())
    return {k:torch.cat(v,dim=0) for k,v in pieces.items()}


def precompute(model,collector,cache,teacher,train_n,dev_n,seed,batch_size,device):
    train={};dev={}
    for tier in TIERS:
        train_rows=fork.science.rows_for_probe(teacher,'train',tier,train_n,seed)
        dev_rows=fork.science.rows_for_probe(teacher,'dev',tier,dev_n,seed)
        train[tier]=capture_rows(model,collector,cache,train_rows,batch_size,device)
        # The original fork scores 48 dev cases in groups of precisely four.
        dev[tier]=capture_rows(model,collector,cache,dev_rows,DEV_BATCH,device)
        fork.emit('internal_ab_features_ready',tier=tier,train=len(train_rows),dev=len(dev_rows))
    return train,dev


def decode_frozen(decoder,prebody):
    """Re-enter the ORIGINAL trained body and its X/Y sign + magnitude banks."""
    features=decoder.body(prebody)
    logits=decoder.bank(features)
    p=F.softmax(logits,dim=-1)
    g=(p*decoder.centers).sum(dim=-1)
    sign_logits=decoder.sign(features)
    label=sign_logits.argmax(-1)
    sign=torch.where(label==0,-1.,torch.where(label==2,1.,0.))
    xy=sign*torch.expm1(g*math.log(10.))
    return {'xy':xy,'log_magnitude':g,'sign':sign_logits,'bank_logits':logits}


def predict(bridge,decoder,data):
    prebody=data['features']
    changed,intermediate=bridge(prebody)
    changed_out=decode_frozen(decoder,changed)
    # Compute the same baseline numerically in the same batch. Its subtraction
    # cancels exact batch-specific rounding when feedback is zero, leaving the
    # historical actual donor predictions as the reference for gate scoring.
    with torch.no_grad():
        old_out=decode_frozen(decoder,prebody)
    predicted_x=data['original_x']+(changed_out['xy'][:,0]-old_out['xy'][:,0])
    predicted_y=data['original_y']+(changed_out['xy'][:,1]-old_out['xy'][:,1])
    return predicted_x,predicted_y,changed_out,intermediate


def objective(bridge,decoder,data,*,sign_weight=.05,y_stability_weight=.1,feedback_weight=1e-4):
    _x,_y,out,latent=predict(bridge,decoder,data)
    target=data['target_x']
    g_loss=F.smooth_l1_loss(out['log_magnitude'][:,0],torch.log10(1+target.abs()),beta=.05)
    labels=torch.where(target<0,0,torch.where(target>0,2,1)).long()
    s_loss=F.cross_entropy(out['sign'][:,0],labels)
    y_loss=F.smooth_l1_loss(out['log_magnitude'][:,1],data['original_g'][:,1],beta=.05)
    fb_loss=latent['feedback'].square().mean()
    return g_loss+sign_weight*s_loss+y_stability_weight*y_loss+feedback_weight*fb_loss


def assess(bridge,decoder,data,gate):
    with torch.no_grad():
        x,y,out,latent=predict(bridge,decoder,data)
        target=data['target_x']
        err=(x-target).abs()/target.abs().clamp_min(1.)
        correct=err<=.05
        small=target.abs()<250000
        large=target.abs()>=1000000
        sign_correct=torch.sign(x)==torch.sign(target)
        def subgroup(mask):
            return float(correct[mask].float().mean()) if bool(mask.any()) else None
        n=int(correct.sum())
        info={'cases':int(target.numel()),'correct':n,'accuracy_5pct':n/len(target),
              'passes_gate':n/len(target)+1e-10>=gate/100,
              'mean_relative_error':float(err.mean()),
              'small_x_5pct':subgroup(small),'large_x_5pct':subgroup(large),
              'sign_accuracy':float(sign_correct.float().mean()),
              'mean_y_relative_drift':float(((y-data['original_y']).abs()/data['original_y'].abs().clamp_min(1.)).mean()),
              'mean_feedback_l2':float(latent['feedback'].square().sum(-1).sqrt().mean())}
        if latent['location'] is not None:
            info['mean_abs_internal_location']=float(latent['location'].abs().mean())
            p=latent['distribution'].clamp_min(1e-12)
            info['register_entropy']=float((-(p*p.log()).sum(-1)).mean())
        return info


def report(arms,decoder,dev,gate,step):
    result={'step':step,'gate_percent':gate,'arms':{}}
    for name,bridge in arms.items():
        bridge.eval()
        scores={t:assess(bridge,decoder,dev[t],gate) for t in TIERS}
        fails=[t for t in TIERS if not scores[t]['passes_gate']]
        result['arms'][name]={'scores':scores,'failing_tiers':fails,
                              'first_failing':fails[0] if fails else None,
                              'all_nine_pass':not fails,
                              'macro_accuracy_5pct':sum(v['accuracy_5pct'] for v in scores.values())/len(scores)}
    return result


def validate_baseline(model,cache,dev,teacher,gate,seed,dev_n):
    expected=fork.evaluate(model,cache,teacher,TIERS,fork.xyop.parse_mixes(['*:*=011:1']),seed,dev_n,gate)
    scores={}
    for tier in TIERS:
        data=dev[tier]
        err=(data['original_x']-data['target_x']).abs()/data['target_x'].abs().clamp_min(1.)
        count=int((err<=.05).sum())
        original=expected['scores'][tier]
        actual_n=int(data['target_x'].numel())
        if int(original['n']) != actual_n or previous.count_from_accuracy(original['joint_5pct'],original['n'])!=count:
            raise ValueError(f'INTERNAL_AB_BASELINE_MISMATCH:{tier}:captured={count}/{actual_n}:'
                             f'original={original["joint_5pct"]}')
        scores[tier]=count/actual_n
    return scores


def validate_args(args):
    if (args.additional_steps<0 or args.chunk_steps<1 or args.views<1 or args.train_per_tier<8
        or args.dev_per_tier<8 or args.batch_size<1 or args.lr<=0 or
        not (0<args.gate_percent<=100) or args.registers<9 or args.registers%2==0 or
        args.limit<=0 or args.sigma_bins<=0 or not (0<args.feedback_scale<=1) or
        args.sign_weight<0 or args.y_stability_weight<0 or args.feedback_weight<0):
        raise ValueError('INTERNAL_AB_INVALID_OPTIONS')
    out=args.output.expanduser().resolve()
    if not all(previous.disjoint(out,x) for x in (args.fork.expanduser().resolve(),
             args.source.expanduser().resolve().parent,args.teacher.expanduser().resolve().parent)):
        raise ValueError('INTERNAL_AB_OUTPUT_OVERLAPS_INPUT')
    return out


def identity(args,raw,donor):
    prior=donor.get('identity')
    if donor.get('schema')!=fork.SCHEMA or not isinstance(prior,dict):
        raise ValueError('INTERNAL_AB_BAD_DONOR_SCHEMA')
    if prior.get('mix')!=['*:*=011:1']:
        raise ValueError('INTERNAL_AB_REQUIRES_HIDDEN_X_VISIBLE_Y_011')
    if fork.fingerprint(args.source)!=prior['source_sha256'] or fork.fingerprint(args.teacher)!=prior['teacher_sha256']:
        raise ValueError('INTERNAL_AB_SOURCE_TEACHER_DRIFT')
    return {'schema':SCHEMA,'origin_fork':str(args.fork.expanduser().resolve()),
            'origin_fork_step':int(donor['fork_step']),'donor_snapshot_sha256':hashlib.sha256(raw).hexdigest(),
            'source':str(args.source.expanduser().resolve()),'source_sha256':prior['source_sha256'],
            'teacher':str(args.teacher.expanduser().resolve()),'teacher_sha256':prior['teacher_sha256'],
            'parent_registers':prior['registers'],'parent_sigma_steps':prior['sigma_steps'],
            'seed':args.seed,'dev_per_tier':args.dev_per_tier,'train_per_tier':args.train_per_tier,
            'views':args.views,'gate_percent':args.gate_percent,'batch_size':args.batch_size,
            'registers':args.registers,'limit':args.limit,'sigma_bins':args.sigma_bins,
            'feedback_scale':args.feedback_scale,'lr':args.lr,
            'sign_weight':args.sign_weight,'y_stability_weight':args.y_stability_weight,
            'feedback_weight':args.feedback_weight,
            'architecture':'shared frozen donor body and X/Y readout, trainable pre-body bridge',
            'arms':['bank','continuous'],'objective':'identical hidden-X magnitude/sign + Y stability + feedback penalty',
            'data_visibility':'X hidden, Y visible; zero embedded arithmetic oracle'}


def run(args):
    out=validate_args(args)
    snap=out/'donor_snapshot.pt';conf=out/'config.json';latest=out/'latest.pt'
    exists=[p.exists() for p in (snap,conf,latest)]
    if any(exists) and not all(exists):raise ValueError('INTERNAL_AB_PARTIAL_RUN_STATE')
    if all(exists):
        raw=snap.read_bytes()
        donor=torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)
        ident=identity(args,raw,donor)
        if json.loads(conf.read_text(encoding='utf8'))!=ident:
            raise ValueError('INTERNAL_AB_RESUME_CONFIG_DRIFT')
        saved=torch.load(latest,map_location='cpu',weights_only=True)
        if saved.get('schema')!=SCHEMA or saved.get('identity')!=ident:
            raise ValueError('INTERNAL_AB_RESUME_STATE_DRIFT')
        step=int(saved['step'])
    else:
        raw=(args.fork.expanduser().resolve()/'latest.pt').read_bytes()
        donor=torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)
        ident=identity(args,raw,donor)
        saved=None
        step=0
    fork.emit('internal_ab_prepare',origin_fork_step=ident['origin_fork_step'],step=step,
              donor_read_only=True,source_read_only=True,snapshot_sha256=ident['donor_snapshot_sha256'])
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    model['decoder']=fork.build_for_resume(model,donor,ident['parent_registers'],ident['parent_sigma_steps'])
    decoder=model['decoder']
    decoder.eval().requires_grad_(False)
    collector=FrozenPreBody(decoder)
    cache_id={'schema':SCHEMA+'.capture-cache','source_sha256':ident['source_sha256'],
              'teacher_sha256':ident['teacher_sha256'],
              'capture_max_tokens':model['config']['max_prompt_tokens']}
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',cache_id,args.cache_memory_mb)
    train,dev=precompute(model,collector,cache,args.teacher,args.train_per_tier,args.dev_per_tier,
                         args.seed,args.batch_size,model['device'])
    baseline=validate_baseline(model,cache,dev,args.teacher,args.gate_percent,args.seed,args.dev_per_tier)
    collector.close()
    width=int(dev[TIERS[0]]['features'].shape[-1])
    torch.manual_seed(args.seed+101)
    bank=IntermediateBridge(width,args.registers,kind='bank',limit=args.limit,
                            sigma_bins=args.sigma_bins,feedback_scale=args.feedback_scale).to(model['device'])
    torch.manual_seed(args.seed+101)
    continuous=IntermediateBridge(width,args.registers,kind='continuous',limit=args.limit,
                                  sigma_bins=args.sigma_bins,feedback_scale=args.feedback_scale).to(model['device'])
    arms={'bank':bank,'continuous':continuous}
    sizes={name:sum(p.numel() for p in head.parameters()) for name,head in arms.items()}
    if sizes['bank']!=sizes['continuous']:raise ValueError('INTERNAL_AB_NOT_PARAMETER_MATCHED')
    opts={name:torch.optim.AdamW(head.parameters(),lr=args.lr) for name,head in arms.items()}
    if saved is not None:
        for name,head in arms.items():
            head.load_state_dict(saved['heads'][name],strict=True)
            opts[name].load_state_dict(saved['optimizers'][name])
    else:
        for tier in TIERS:
            for name,head in arms.items():
                state=assess(head,decoder,dev[tier],args.gate_percent)
                expected=previous.count_from_accuracy(baseline[tier],state['cases'])
                if state['correct']!=expected or state['mean_feedback_l2']!=0.:
                    raise ValueError('INTERNAL_AB_IDENTITY_SCORE_MISMATCH:'+tier+':'+name)
        out.mkdir(parents=True,exist_ok=True)
        previous.atomic_bytes(snap,raw)
        previous.atom_json(conf,ident)
        previous.atom_json(out/'donor-baseline.json',baseline)
        fork.emit('internal_ab_initialized',zero_feedback_verified=True,parameter_matched=True,
                  parameters_per_arm=sizes['bank'],bank_registers=args.registers,
                  gate_percent=args.gate_percent)
    def save(s):
        previous.atom_torch(latest,{'schema':SCHEMA,'identity':ident,'step':s,
                           'heads':{name:head.state_dict() for name,head in arms.items()},
                           'optimizers':{name:opt.state_dict() for name,opt in opts.items()},
                           'rng_cpu':torch.get_rng_state(),
                           'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None})
    if saved is None:save(0)
    else:
        torch.set_rng_state(saved['rng_cpu'])
        if args.device=='cuda' and saved.get('rng_cuda') is not None:
            torch.cuda.set_rng_state_all(saved['rng_cuda'])
    current=report(arms,decoder,dev,args.gate_percent,step)
    if not args.additional_steps:
        fork.emit('internal_ab_probe',step=step,
             bank_accuracy=current['arms']['bank']['macro_accuracy_5pct'],
             continuous_accuracy=current['arms']['continuous']['macro_accuracy_5pct'])
        return
    for target in range(step+1,step+args.additional_steps+1):
        examples=[]
        for j in range(args.views):
            tier=TIERS[(target*args.views+j)%len(TIERS)]
            dataset=train[tier]
            rng=random.Random(f'internal-ab/{args.seed}/{target}/{j}/{tier}')
            examples.append((tier,rng.randrange(len(dataset['target_x']))))
        sample={key:torch.stack([train[tier][key][i] for tier,i in examples],dim=0)
                for key in train[TIERS[0]]}
        for name,head in arms.items():
            head.train()
            optimizer=opts[name]
            optimizer.zero_grad(set_to_none=True)
            loss=objective(head,decoder,sample,sign_weight=args.sign_weight,
                           y_stability_weight=args.y_stability_weight,
                           feedback_weight=args.feedback_weight)
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError('INTERNAL_AB_NONFINITE_LOSS')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(head.parameters(),1.)
            optimizer.step()
        if target%args.chunk_steps==0 or target==step+args.additional_steps:
            current=report(arms,decoder,dev,args.gate_percent,target)
            save(target)
            previous.atom_json(out/f'probe-step-{target:07d}.json',current)
            b=current['arms']['bank'];c=current['arms']['continuous']
            fork.emit('internal_ab_probe',step=target,origin_fork_step=ident['origin_fork_step'],
                 bank_accuracy=b['macro_accuracy_5pct'],continuous_accuracy=c['macro_accuracy_5pct'],
                 bank_failing=b['failing_tiers'],continuous_failing=c['failing_tiers'],
                 bank_t08=b['scores']['T08']['accuracy_5pct'],
                 continuous_t08=c['scores']['T08']['accuracy_5pct'],
                 bank_small_t08=b['scores']['T08']['small_x_5pct'],
                 continuous_small_t08=c['scores']['T08']['small_x_5pct'],
                 checkpoint=str(latest))
    if hashlib.sha256(snap.read_bytes()).hexdigest()!=ident['donor_snapshot_sha256']:
        raise ValueError('INTERNAL_AB_SNAPSHOT_CHANGED')
    fork.emit('internal_ab_finished',step=step+args.additional_steps,
              immutable_donor_snapshot_verified=True,final_report=str(out/f'probe-step-{step+args.additional_steps:07d}.json'))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fork',type=Path,default=fork.DEFAULT_OUTPUT,help='Read-only Gaussian fork donor root')
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--additional-steps',type=int,default=3000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--views',type=int,default=4)
    p.add_argument('--train-per-tier',type=int,default=256)
    p.add_argument('--dev-per-tier',type=int,default=48)
    p.add_argument('--batch-size',type=int,default=8)
    p.add_argument('--gate-percent',type=float,default=40.)
    p.add_argument('--registers',type=int,default=129)
    p.add_argument('--limit',type=float,default=.4)
    p.add_argument('--sigma-bins',type=float,default=2.)
    p.add_argument('--feedback-scale',type=float,default=.1)
    p.add_argument('--sign-weight',type=float,default=.05)
    p.add_argument('--y-stability-weight',type=float,default=.1)
    p.add_argument('--feedback-weight',type=float,default=1e-4)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    return p


def main(argv=None):
    run(parser().parse_args(argv))


if __name__=='__main__':main()
