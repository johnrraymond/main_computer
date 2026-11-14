#!/usr/bin/env python3
"""Isolated frozen-donor A/B test: scalar vs 129 signed Gaussian correction registers.

No predictor contains the Hawk-Dove oracle; truth is used only to train/score.
Neither the live fork checkpoint nor its optimizer is modified. All new data
and optimizer states live in --output. Input pattern 011 hides X, reveals Y.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import tempfile

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_hawk_dove_gaussian_register_fork as fork

SCHEMA='nanojev.hawk-dove.gaussian-correction-ab.v1'
DEFAULT_OUTPUT=Path.home()/'NanoJev/runs/hawk_dove_gaussian_correction_ab_v1'
LN10=math.log(10.)
TIERS=tuple(fork.hd.TIERS)
# fork.evaluate scores held-out examples in groups of exactly four. The
# decoder's float16/CUDA batched operations are not guaranteed batch invariant.
ORIGINAL_DEV_PROBE_BATCH_SIZE=4


def sha_bytes(data:bytes)->str:
    return hashlib.sha256(data).hexdigest()


def emit(event,**kw):
    print(json.dumps({'event':event,**kw},sort_keys=True,allow_nan=False),flush=True)


def atomic_bytes(path:Path,data:bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix='correction-',suffix='.tmp',delete=False) as f:
        temp=Path(f.name)
        f.write(data)
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def atom_json(path,value):
    atomic_bytes(path,(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n').encode())


def atom_torch(path,value):
    stream=io.BytesIO()
    torch.save(value,stream)
    atomic_bytes(path,stream.getvalue())


def disjoint(a:Path,b:Path):
    a=a.resolve();b=b.resolve()
    return not (a==b or a in b.parents or b in a.parents)


class ScalarCorrection(nn.Module):
    """Direct input-dependent scalar; capacity-matched *input*, not parameters."""
    def __init__(self,width:int,limit:float=.4):
        super().__init__()
        self.readout=nn.Linear(width,1)
        nn.init.zeros_(self.readout.weight)
        nn.init.zeros_(self.readout.bias)
        self.limit=float(limit)

    def forward(self,features):
        return self.limit*torch.tanh(self.readout(features).squeeze(-1))


class SignedGaussianCorrection(nn.Module):
    """Distribution over signed log10 corrections; weighted mean is readout."""
    def __init__(self,width:int,count:int=129,limit:float=.4,sigma_bins:float=2.):
        super().__init__()
        if count<9 or count%2==0 or limit<=0 or sigma_bins<=0:
            raise ValueError('CORRECTION_INVALID_REGISTER_CONFIGURATION')
        self.readout=nn.Linear(width,count)
        nn.init.zeros_(self.readout.weight)
        nn.init.zeros_(self.readout.bias)
        centers=torch.linspace(-limit,limit,count)
        self.register_buffer('centers',centers)
        self.register_buffer('uniform_mean', (centers * F.softmax(torch.zeros_like(centers),dim=-1)).sum())
        self.sigma=float((centers[1]-centers[0]).item())*sigma_bins
        self.limit=float(limit)

    def forward(self,features):
        logits=self.readout(features)
        probabilities=F.softmax(logits,dim=-1)
        correction=(probabilities*self.centers).sum(dim=-1)-self.uniform_mean
        return correction,logits

    def gaussian_loss(self,logits,true_delta):
        desired=true_delta.detach().clamp(-self.limit,self.limit)
        q=F.softmax(-.5*((self.centers.view(1,-1)-desired[:,None])/self.sigma).square(),dim=-1)
        return F.kl_div(F.log_softmax(logits,dim=-1),q,reduction='batchmean')


class FrozenFeatures(nn.Module):
    """Capture the real trained intermediate features entering the donor bank."""
    def __init__(self,decoder):
        super().__init__()
        self.decoder=decoder
        self.features=None
        self.hook=self.decoder.bank.register_forward_pre_hook(self._capture)
        self.decoder.eval()
        self.decoder.requires_grad_(False)

    def _capture(self,mod,args):
        self.features=args[0].detach()

    def extract(self,model,prompts,channels,cache):
        self.features=None
        with torch.no_grad():
            prediction=fork.forward_masked(model,prompts,channels,cache)
            if self.features is None or self.features.shape[:2]!=(len(prompts),2):
                raise ValueError('CORRECTION_FROZEN_FEATURE_HOOK_FAILED')
            x_feature=self.features[:,0,:].float()
            g=prediction['log_magnitude'][:,0].float()
            p=F.softmax(prediction['bank_logits'][:,0,:].float(),dim=-1)
            centers=self.decoder.centers.float().view(1,-1)
            spread=torch.sqrt(((centers-g[:,None]).square()*p).sum(-1).clamp_min(0.))
            entropy=-(p*torch.log(p.clamp_min(1e-12))).sum(-1)/math.log(p.shape[-1])
            # Three bank-derived inputs: magnitude, spread, normalized entropy.
            features=torch.cat([x_feature,(g/7.)[:,None],spread[:,None],entropy[:,None]],dim=1)
            sign_class=prediction['sign'][:,0,:].argmax(-1)
            sign=torch.where(sign_class==0,-1.,torch.where(sign_class==2,1.,0.))
            original_x=prediction['xy'][:,0].float()
            return {'features':features,'coarse_g':g,'sign':sign,'original_x':original_x}

    def close(self):
        self.hook.remove()


def reconstruct(sign,g):
    return sign*torch.expm1(g.clamp(0,math.log10(1+fork.MAX_ABS))*LN10)


def sample_loss(correction,coarse_g,target):
    new_g=(coarse_g+correction).clamp(0,math.log10(1+fork.MAX_ABS))
    return F.smooth_l1_loss(new_g,torch.log10(1+target.abs()),beta=.05)


def rows_to_features(model,collector,cache,rows,batch_size:int,device):
    parts={k:[] for k in ('features','coarse_g','sign','original_x','target')}
    for begin in range(0,len(rows),batch_size):
        chunk=rows[begin:begin+batch_size]
        for r in chunk:
            if not fork.rt.token_eligible(model,r['prompt']):
                raise ValueError('CORRECTION_INELIGIBLE_PROMPT:'+str(r['case_id']))
        channels=[fork.xyop.pack(r['x'],r['y'],'011') for r in chunk]
        f=collector.extract(model,[r['prompt'] for r in chunk],channels,cache)
        f['target']=torch.as_tensor([float(r['x']) for r in chunk],device=device,dtype=torch.float32)
        for key,val in f.items():
            parts[key].append(val.detach())
    if not rows:raise ValueError('CORRECTION_EMPTY_DATASET')
    return {k:torch.cat(v,dim=0) for k,v in parts.items()}


def predict(head,features,coarse_g,sign,original_x):
    if isinstance(head,SignedGaussianCorrection):
        delta,logits=head(features)
    else:
        delta=head(features)
        logits=None
    # Use the donor's *actual* prediction as the identity anchor. Rounding
    # either g or expm1(g) a second time can flip a near-5%-boundary case.
    # At delta=0 this is exactly original_x; for nonzero delta the correction
    # is smooth and keeps the frozen sign unchanged.
    corrected=original_x+(reconstruct(sign,coarse_g+delta)-reconstruct(sign,coarse_g))
    return corrected,delta,logits


def analyze(head,data,gate:float):
    with torch.no_grad():
        pred,delta,_=predict(head,data['features'],data['coarse_g'],data['sign'],data['original_x'])
        truth=data['target']
        err=(pred-truth).abs()/truth.abs().clamp_min(1.)
        correct=(err<=.05)
        small=truth.abs()<250000
        large=truth.abs()>=1000000
        original_err=(data['original_x']-truth).abs()/truth.abs().clamp_min(1.)
        def frac(mask):
            return float(correct[mask].float().mean()) if bool(mask.any()) else None
        return {'accuracy_5pct':float(correct.float().mean()),'correct':int(correct.sum()),
                'cases':int(len(truth)),'passes_gate':float(correct.float().mean())+1e-10>=gate/100,
                'mean_relative_error':float(err.mean()),'small_x_5pct':frac(small),
                'large_x_5pct':frac(large),'mean_signed_correction_log10':float(delta.mean()),
                'baseline_accuracy_5pct':float((original_err<=.05).float().mean()),
                'sign_accuracy':float((torch.sign(truth)==data['sign']).float().mean())}


def dataset_report(arms,dev,gate,step):
    report={'step':step,'gate_percent':gate,'arms':{}}
    for name,head in arms.items():
        head.eval()
        scores={tier:analyze(head,dev[tier],gate) for tier in TIERS}
        failures=[tier for tier in TIERS if not scores[tier]['passes_gate']]
        report['arms'][name]={'scores':scores,'all_nine_pass':not failures,
                               'first_failing':failures[0] if failures else None,
                               'failing_tiers':failures,
                               'macro_accuracy_5pct':sum(v['accuracy_5pct'] for v in scores.values())/len(scores)}
    return report


def precompute(model,collector,cache,teacher,train_per_tier,dev_per_tier,seed,batch_size,device):
    train={};dev={}
    for tier in TIERS:
        a=fork.science.rows_for_probe(teacher,'train',tier,train_per_tier,seed)
        b=fork.science.rows_for_probe(teacher,'dev',tier,dev_per_tier,seed)
        train[tier]=rows_to_features(model,collector,cache,a,batch_size,device)
        # Match the original fork.evaluate held-out inference batching exactly.
        # Training examples still use the requested capture batch size.
        dev[tier]=rows_to_features(model,collector,cache,b,ORIGINAL_DEV_PROBE_BATCH_SIZE,device)
        emit('correction_features_ready',tier=tier,train=len(a),dev=len(b))
    return train,dev


def count_from_accuracy(score, cases):
    """Compare identical fixed probes by cases, not float32-vs-float64 fractions.

    With the required single 011 task per case, a valid accuracy is an integer
    multiple of 1/cases. Never round away an actual one-case disagreement.
    """
    if not isinstance(cases, int) or cases <= 0 or not math.isfinite(float(score)):
        raise ValueError('CORRECTION_BASELINE_INVALID_SCORE')
    count_float = float(score) * cases
    count = round(count_float)
    if not 0 <= count <= cases or abs(count_float - count) > 1e-4:
        raise ValueError('CORRECTION_BASELINE_INVALID_SCORE')
    return count


def validate_baseline(model,cache,dev,teacher,gate,seed,n):
    # Independent check against the original trainer's actual scoring function.
    ground=fork.evaluate(model,cache,teacher,TIERS,fork.xyop.parse_mixes(['*:*=011:1']),seed,n,gate)
    identity=ScalarCorrection(dev['T00']['features'].shape[-1]).to(dev['T00']['features'].device)
    for tier in TIERS:
        assessed=analyze(identity,dev[tier],gate)
        ours=assessed['accuracy_5pct']
        original_info=ground['scores'][tier]
        original=original_info['joint_5pct']
        original_cases=original_info['n']
        if original_cases!=assessed['cases'] or count_from_accuracy(original,original_cases)!=assessed['correct']:
            raise ValueError(
                f'CORRECTION_BASELINE_SCORE_MISMATCH:{tier}:'
                f'captured={assessed["correct"]}/{assessed["cases"]}:'
                f'original={count_from_accuracy(original,original_cases)}/{original_cases}:'
                f'captured_accuracy={ours}:original_accuracy={original}'
            )
    return {tier:ground['scores'][tier]['joint_5pct'] for tier in TIERS}


def _config(args,snapshot_bytes,donor_ck):
    ident=donor_ck.get('identity')
    if not isinstance(ident,dict) or donor_ck.get('schema')!=fork.SCHEMA:
        raise ValueError('CORRECTION_NOT_GAUSSIAN_FORK_CHECKPOINT')
    source=args.source.resolve();teacher=args.teacher.resolve()
    if fork.fingerprint(source)!=ident['source_sha256'] or fork.fingerprint(teacher)!=ident['teacher_sha256']:
        raise ValueError('CORRECTION_SOURCE_TEACHER_DRIFT')
    if ident['mix']!=['*:*=011:1']:
        raise ValueError('CORRECTION_REQUIRES_HIDDEN_X_VISIBLE_Y_MIX_011')
    return {'schema':SCHEMA,'origin_fork':str(args.fork.resolve()),
            'origin_fork_step':int(donor_ck['fork_step']),'donor_snapshot_sha256':sha_bytes(snapshot_bytes),
            'source':str(source),'source_sha256':ident['source_sha256'],
            'teacher':str(teacher),'teacher_sha256':ident['teacher_sha256'],
            'seed':args.seed,'train_per_tier':args.train_per_tier,'dev_per_tier':args.dev_per_tier,
            'registers':args.registers,'limit':args.limit,'sigma_bins':args.sigma_bins,
            'gaussian_weight':args.gaussian_weight,'learning_rate':args.lr,'batch_views':args.views,
            'gate_percent':args.gate_percent,'parent_registers':ident['registers'],
            'parent_sigma_steps':ident['sigma_steps'],'architecture':'frozen_input_body_sign_and_512_register_bank',
            'control':'trainable_input_conditional_scalar_correction',
            'treatment':'129_signed_gaussian_correction_registers',
            'training_target':'hidden_X_only','validation':'fixed_original_dev_rows_no_test_rows'}


def load_checkpoint(raw):
    return torch.load(io.BytesIO(raw),map_location='cpu',weights_only=True)


def _validate_args(args):
    if args.additional_steps<0 or args.chunk_steps<1 or args.views<1 or args.train_per_tier<8 or args.dev_per_tier<8 or args.batch_size<1 or args.lr<=0 or not (0<args.gate_percent<=100) or not (0<=args.gaussian_weight<=1) or args.registers<9 or args.registers%2==0 or args.limit<=0 or args.sigma_bins<=0:
        raise ValueError('CORRECTION_BAD_OPTIONS')
    out=args.output.expanduser().resolve()
    forbidden=(args.fork.expanduser().resolve(),args.source.expanduser().resolve().parent,
               args.teacher.expanduser().resolve().parent)
    if any(not disjoint(out,b) for b in forbidden):
        raise ValueError('CORRECTION_OUTPUT_OVERLAPS_SOURCE_OR_DONOR')
    return out


def run(args):
    out=_validate_args(args)
    snapshot=out/'donor_snapshot.pt';config=out/'config.json';checkpoint=out/'latest.pt'
    already=[x.exists() for x in (snapshot,config,checkpoint)]
    if any(already) and not all(already):
        raise ValueError('CORRECTION_PARTIAL_RUN_STATE')
    if all(already):
        saved=json.loads(config.read_text(encoding='utf-8'))
        raw=snapshot.read_bytes();ck=load_checkpoint(raw)
        expectation=_config(args,raw,ck)
        if expectation!=saved:raise ValueError('CORRECTION_RESUME_CONFIG_DRIFT')
        state=torch.load(checkpoint,map_location='cpu',weights_only=True)
        if state.get('schema')!=SCHEMA or state.get('identity')!=saved:
            raise ValueError('CORRECTION_RESUME_CHECKPOINT_DRIFT')
        step=int(state['step'])
    else:
        raw=(args.fork.expanduser().resolve()/'latest.pt').read_bytes()
        ck=load_checkpoint(raw);saved=_config(args,raw,ck)
        step=0
    emit('correction_ab_prepare',origin_fork_step=saved['origin_fork_step'],experiment_step=step,
         source_read_only=True,donor_read_only=True,snapshot_sha256=saved['donor_snapshot_sha256'])
    model=fork.ex.load_current(args.source,args.device,args.dtype,args.allow_model_download,args.seed)
    model['decoder']=fork.build_for_resume(model,ck,saved['parent_registers'],saved['parent_sigma_steps'])
    model['decoder'].eval().requires_grad_(False)
    collector=FrozenFeatures(model['decoder'])
    cache_id={'schema':SCHEMA+'.capture-cache','source_sha256':saved['source_sha256'],
              'teacher_sha256':saved['teacher_sha256'],
              'capture_max_tokens':model['config']['max_prompt_tokens']}
    # This directory is entirely independent from the live Gaussian fork.
    cache=fork.cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',cache_id,args.cache_memory_mb)
    train,dev=precompute(model,collector,cache,args.teacher,args.train_per_tier,args.dev_per_tier,
                         args.seed,args.batch_size,model['device'])
    baseline=validate_baseline(model,cache,dev,args.teacher,args.gate_percent,args.seed,args.dev_per_tier)
    width=dev[TIERS[0]]['features'].shape[-1]
    scalar=ScalarCorrection(width,args.limit).to(model['device'])
    bank=SignedGaussianCorrection(width,args.registers,args.limit,args.sigma_bins).to(model['device'])
    arms={'scalar':scalar,'bank':bank}
    opts={k:torch.optim.AdamW(v.parameters(),lr=args.lr) for k,v in arms.items()}
    if all(already):
        for k in arms:
            arms[k].load_state_dict(state['heads'][k],strict=True)
            opts[k].load_state_dict(state['optimizers'][k])
    else:
        for tier in TIERS:
            for head in arms.values():
                assessed=analyze(head,dev[tier],args.gate_percent)
                if assessed['correct']!=count_from_accuracy(baseline[tier],assessed['cases']):
                    raise ValueError('CORRECTION_NOT_IDENTITY_INITIALIZED:'+tier)
        out.mkdir(parents=True,exist_ok=True)
        atomic_bytes(snapshot,raw)
        atom_json(config,saved)
        atom_json(out/'donor-baseline.json',baseline)
        emit('correction_ab_initialized',zero_correction_verified=True,
             scalar_parameters=sum(p.numel() for p in scalar.parameters()),
             bank_parameters=sum(p.numel() for p in bank.parameters()),
             gate_percent=args.gate_percent)
    # Dataset tensors are cached in GPU RAM; no model gradients, no numerical oracle at inference.
    def save(s):
        atom_torch(checkpoint,{'schema':SCHEMA,'identity':saved,'step':s,
                               'heads':{k:v.state_dict() for k,v in arms.items()},
                               'optimizers':{k:v.state_dict() for k,v in opts.items()}})
    if not all(already):save(0)
    report=dataset_report(arms,dev,args.gate_percent,step)
    if args.additional_steps==0:
        emit('correction_ab_probe',step=step,**report['arms'])
        collector.close();return
    for target in range(step+1,step+args.additional_steps+1):
        # Balanced, deterministic rotation. Over 9 steps every tier appears
        # equally often, and BOTH heads receive precisely the same examples.
        chunks=[]
        for j in range(args.views):
            tier=TIERS[(target*args.views+j)%len(TIERS)]
            data=train[tier];rng=random.Random(f'correction/{args.seed}/{target}/{j}/{tier}')
            i=rng.randrange(len(data['target']))
            chunks.append((tier,i))
        feature=torch.stack([train[t]['features'][i] for t,i in chunks])
        coarse=torch.stack([train[t]['coarse_g'][i] for t,i in chunks])
        truth=torch.stack([train[t]['target'][i] for t,i in chunks])
        for name,head in arms.items():
            head.train();opt=opts[name];opt.zero_grad(set_to_none=True)
            if name=='bank':
                correction,logits=head(feature)
                target_delta=torch.log10(1+truth.abs())-coarse
                gaussian=head.gaussian_loss(logits,target_delta)
                loss=sample_loss(correction,coarse,truth)+args.gaussian_weight*gaussian
            else:
                correction=head(feature)
                loss=sample_loss(correction,coarse,truth)
            if not torch.isfinite(loss):raise FloatingPointError('CORRECTION_NONFINITE_LOSS')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(head.parameters(),1.)
            opt.step()
        if target%args.chunk_steps==0 or target==step+args.additional_steps:
            report=dataset_report(arms,dev,args.gate_percent,target)
            save(target)
            atom_json(out/f'probe-step-{target:07d}.json',report)
            emit('correction_ab_probe',step=target,origin_fork_step=saved['origin_fork_step'],
                 scalar_accuracy=report['arms']['scalar']['macro_accuracy_5pct'],
                 bank_accuracy=report['arms']['bank']['macro_accuracy_5pct'],
                 scalar_failing=report['arms']['scalar']['failing_tiers'],
                 bank_failing=report['arms']['bank']['failing_tiers'],
                 scalar_t08=report['arms']['scalar']['scores']['T08']['accuracy_5pct'],
                 bank_t08=report['arms']['bank']['scores']['T08']['accuracy_5pct'],
                 scalar_all_nine_pass=report['arms']['scalar']['all_nine_pass'],
                 bank_all_nine_pass=report['arms']['bank']['all_nine_pass'],
                 checkpoint=str(checkpoint))
    collector.close()
    emit('correction_ab_finished',step=step+args.additional_steps,checkpoint_immutable_donor=True,
         final_report=str(out/f'probe-step-{step+args.additional_steps:07d}.json'))


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fork',type=Path,default=fork.DEFAULT_OUTPUT,help='Existing Gaussian-register fork root, read-only')
    p.add_argument('--source',type=Path,default=fork.science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=fork.science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    p.add_argument('--dtype',choices=['float16','bfloat16','float32'],default='float16')
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
    p.add_argument('--gaussian-weight',type=float,default=.025)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    return p


def main(argv=None):
    run(parser().parse_args(argv))


if __name__=='__main__':
    main()
