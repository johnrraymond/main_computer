#!/usr/bin/env python3
"""Isolated Gaussian magnitude-register experiment for NanoJev Hawk-Dove.

Train a NEW head and optimizer, seeded with the verified V3 attention/body/sign
and XYOP input adapters. No mantissa/exponent modules, targets, or losses are
constructed. Frozen-Qwen captures, the trained numerical adapters, and hidden-X
advancement remain comparable to the donor V3 run. All writes are under --output.

The donor's numeric sidecar is NEVER instantiated or used for inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import tempfile

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_altreg_math_v3_decoder_experiment as v3
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_hawk_dove_cached_train as cached
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_numeric_sidecar_train as sidecar
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_xyop_mask_train as xyop
from tools import nanojev_game_math_curriculum_v10_runtime as rt

SCHEMA = 'nanojev.hawk-dove.gaussian-register-fork.v1'
MAX_ABS = 10_000_000.0
DEFAULT_OUTPUT = Path.home() / 'NanoJev/runs/hawk_dove_gaussian_register_fork_v1'


def fingerprint(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def emit(event, **fields):
    print(json.dumps({'event':event,**fields},sort_keys=True,allow_nan=False),flush=True)


def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temp,path)


def atomic_torch(path, value):
    with tempfile.NamedTemporaryFile('wb',dir=path.parent,prefix='register-',suffix='.tmp',delete=False) as f:
        temp=Path(f.name)
    try:
        torch.save(value,temp)
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def parse_mask(text):
    # A missing mask means ALL tiers are active on THIS invocation.
    if text is None:return ()
    if text.strip().lower() in ('none',''):return ()
    raw=[x.strip().upper() for x in text.split(',')]
    if len(set(raw))!=len(raw) or any(x not in hd.TIERS for x in raw):
        raise ValueError('INVALID_MASK_LEVELS')
    result=tuple(t for t in hd.TIERS if t in raw)
    if len(result)==len(hd.TIERS):raise ValueError('CANNOT_MASK_ALL_TIERS')
    return result


def effective_curriculum(args, checkpoint=None, *, initial_gate=None):
    """Apply invocation mask and persistent, progressive gate configuration.

    Omitted --mask-levels means ALL tiers. On resume, a CHANGED --gate-percent
    becomes the new starting gate without resetting weights/optimizer/step.
    Repeating that same option on later restarts preserves the advanced stage.
    Use --reset-gate to force the specified value even when unchanged.
    """
    masked=parse_mask(args.mask_levels)
    if args.reset_gate and args.gate_percent is None:
        raise ValueError('REGISTER_RESET_GATE_REQUIRES_GATE_PERCENT')
    if checkpoint is None:
        gate=5.0 if args.gate_percent is None else float(args.gate_percent)
    else:
        old_setting=float(checkpoint.get('gate_setting_percent',
                                         initial_gate if initial_gate is not None else checkpoint['gate_percent']))
        if args.gate_percent is None or (not args.reset_gate and float(args.gate_percent)==old_setting):
            gate=float(checkpoint['gate_percent'])
        else:
            gate=float(args.gate_percent)
    if not math.isfinite(gate) or not 0 < gate <= 100:
        raise ValueError('REGISTER_BAD_GATE_PERCENT')
    return masked,gate


class GaussianRegisterDecoder(nn.Module):
    """Trained V3 evidence path + fresh, differentiable magnitude distribution."""
    def __init__(self, donor, registers=512, sigma_steps=2., max_abs=MAX_ABS):
        super().__init__()
        if getattr(donor,'arm',None)!='attention' or registers<8 or sigma_steps<=0 or max_abs<=1:
            raise ValueError('REGISTER_BANK_INVALID_CONFIGURATION')
        self.op_count=donor.op_count
        self.slot=donor.slot
        self.key=donor.key
        self.value=donor.value
        self.queries=donor.queries
        self.body=donor.body
        self.sign=donor.sign
        self.numeric_adapter=donor.numeric_adapter
        self.xyop_mask_adapter=donor.xyop_mask_adapter
        self.bank=nn.Linear(self.sign.in_features,registers)
        nn.init.zeros_(self.bank.weight)
        nn.init.zeros_(self.bank.bias)
        self.register_count=int(registers)
        self.max_abs=float(max_abs)
        self.sigma_steps=float(sigma_steps)
        centers=torch.linspace(0,math.log10(1+max_abs),registers,dtype=torch.float32)
        self.register_buffer('centers',centers)
        self.sigma=float((centers[1]-centers[0]).item())*sigma_steps

    def forward(self,h,parent_xy,operations,tokens,mask):
        b=h.shape[0]
        if parent_xy.shape!=(b,2):raise ValueError('REGISTER_PARENT_SHAPE')
        slots=torch.arange(2,device=h.device)
        keys=self.key(tokens.float());values=self.value(tokens.float())
        scores=torch.einsum('bld,rd->brl',keys,self.queries)/math.sqrt(keys.shape[-1])
        weights=F.softmax(scores.masked_fill(~mask.bool()[:,None,:],-1e4),dim=-1)
        pooled=torch.einsum('brl,bld->brd',weights,values)
        features=self.body(torch.cat([
            h[:,None,:].expand(-1,2,-1),parent_xy[:,:,None].detach(),
            F.one_hot(operations,num_classes=self.op_count).float()[:,None,:].expand(-1,2,-1),
            self.slot(slots)[None,:,:].expand(b,-1,-1),pooled],dim=-1))
        logits=self.bank(features)
        p=F.softmax(logits,dim=-1)
        g=(p*self.centers).sum(-1)
        s=self.sign(features)
        label=s.argmax(-1)
        sign=torch.where(label==0,-1.,torch.where(label==2,1.,0.))
        xy=sign*torch.expm1(g*math.log(10.))
        return {'sign':s,'bank_logits':logits,'log_magnitude':g,'xy':xy}

    def objective(self,out,true_xy,gaussian_weight=.1,magnitude_weight=1.):
        target=torch.as_tensor(true_xy,device=out['xy'].device,dtype=torch.float32)
        if target.shape!=out['xy'].shape or not bool(torch.isfinite(target).all()):
            raise ValueError('REGISTER_TARGET_INVALID')
        label=torch.where(target<0,0,torch.where(target>0,2,1)).long()
        sign_loss=F.cross_entropy(out['sign'].reshape(-1,3),label.reshape(-1))
        g=torch.log10(1+target.abs())
        magnitude=F.smooth_l1_loss(out['log_magnitude'],g,beta=.05)
        # Continuous Gaussian pressure on ALL neighboring registers, never a
        # one-hot exponent target or a periodic fractional mantissa target.
        distance=(self.centers.view(1,1,-1)-g.unsqueeze(-1))/self.sigma
        q=F.softmax(-.5*distance.square(),dim=-1)
        gaussian=F.kl_div(F.log_softmax(out['bank_logits'],dim=-1),q,reduction='none').sum(-1).mean()
        loss=sign_loss+magnitude_weight*magnitude+gaussian_weight*gaussian
        return loss,{'sign':float(sign_loss.detach()),'log_magnitude':float(magnitude.detach()),
                     'gaussian_pressure':float(gaussian.detach())}


def check_no_factorized_head(dec):
    forbidden=('exponent','mantissa','fraction','numeric_sidecar')
    keys=dec.state_dict().keys()
    if any(any(part in k for part in forbidden) for k in keys):
        raise ValueError('REGISTER_BANK_FACTOR_HEAD_PRESENT')


def build_from_donor(model, ck, count, sigma_steps):
    # Donor contains the old heads, but we select only the trained shared path
    # and sign classifier. Never load sidecar weights or use sidecar predictions.
    adapter.verify_saved(ck);sidecar.verify_sidecar(ck);xyop.verify(ck)
    dec=model['decoder']
    width=adapter.checkpoint_width(ck)
    adapter.attach(model,expected_width=width)
    dec.add_module('xyop_mask_adapter',xyop.make_module(width).to(model['device']))
    existing=set(dec.state_dict())
    selected={k:v for k,v in ck['decoder'].items() if k in existing}
    if set(selected)!=existing:
        raise ValueError('REGISTER_DONOR_SHARED_STATE_MISSING:'+str(sorted(existing-set(selected))))
    dec.load_state_dict(selected,strict=True)
    new=GaussianRegisterDecoder(dec,count,sigma_steps).to(model['device'])
    check_no_factorized_head(new)
    return new,dec


def build_for_resume(model, ck, count, sigma_steps):
    # Only saved fork weights are used; donor latest.pt can keep training.
    dec=model['decoder']
    width=adapter.adapter_width(dec,n_ops=dec.op_count)
    adapter.attach(model,expected_width=width)
    dec.add_module('xyop_mask_adapter',xyop.make_module(width).to(model['device']))
    bank=GaussianRegisterDecoder(dec,count,sigma_steps).to(model['device'])
    bank.load_state_dict(ck['decoder'],strict=True)
    check_no_factorized_head(bank)
    return bank


def forward_masked(model, prompts, channels, feature_cache):
    if len(prompts)!=len(channels):raise ValueError('REGISTER_MASK_INPUT_MISMATCH')
    dec=model['decoder'];device=model['device']
    flat, seq, mask = feature_cache.capture(model,prompts)
    with torch.no_grad():
        h=model['parent'].extract(flat.to(device))
        pxy=model['parent'].xy_coord(h)
    # Same numeric adapter + XYOP hint placement used in the original V3 run.
    atom=torch.as_tensor([adapter.encode(p) for p in prompts],dtype=torch.float32,device=device)
    xy_hint=torch.as_tensor(channels,dtype=torch.float32,device=device)
    h=h+dec.numeric_adapter(atom)+dec.xyop_mask_adapter(xy_hint)
    op=torch.full((len(prompts),),model['base'].OP_IDS['written_sub'],device=device,dtype=torch.long)
    return dec(h,pxy,op,seq.to(device),mask.to(device))


def select_training_rows(teacher,focus,step,seed,views,active):
    # Exact scientific replay policy, but excluded tiers never re-enter replay.
    rng=random.Random(f'hd-train/{seed}/{step}')
    previous=active[:active.index(focus)]
    chosen=[]
    for i in range(views):
        tier=focus if i<max(1,views-1) or not previous else rng.choice(previous)
        rows=hd.read_rows(teacher,'train',tier)
        if not rows:raise ValueError('REGISTER_NO_TRAIN_ROWS:'+tier)
        chosen.append(rows[rng.randrange(len(rows))])
    return chosen


def evaluate(model,cache,teacher,active,mixes,seed,n,gate,*,decoder=None):
    if decoder is not None:model['decoder']=decoder
    model['decoder'].eval()
    result={};first=None
    with torch.no_grad():
        for tier in hd.TIERS:
            rows=science.rows_for_probe(teacher,'dev',tier,n,seed)
            tasks=[]
            for row in rows:
                if not rt.token_eligible(model,row['prompt']):raise ValueError('REGISTER_DEV_INELIGIBLE:'+tier)
                mixture=xyop.resolved_mix(mixes,tier,row['mask'])
                total=sum(weight for _,weight in mixture)
                for pattern,weight in mixture:
                    if pattern[:2]=='11':continue
                    tasks.append((row,pattern,weight/total))
            denominator=sum(w for _,_,w in tasks)
            if denominator<=0:raise ValueError('REGISTER_NO_HIDDEN_DEV_TARGET:'+tier)
            correct=0.;errors=[];cases=[]
            for i in range(0,len(tasks),4):
                group=tasks[i:i+4]
                channels=[xyop.pack(r['x'],r['y'],pattern) for r,pattern,_ in group]
                outputs=forward_masked(model,[r['prompt'] for r,_,_ in group],channels,cache)
                pred=outputs['xy'].detach().cpu().tolist() if decoder is None else v3.compose_logits(
                    outputs['sign'],outputs['exponent'],outputs['fraction'],MAX_ABS).detach().cpu().tolist()
                for (row,pattern,w),(px,py) in zip(group,pred):
                    x,y=float(row['x']),float(row['y'])
                    okx=abs(px-x)/max(1.,abs(x))<=.05
                    oky=abs(py-y)/max(1.,abs(y))<=.05
                    correct+=w*int((pattern[0]=='1' or okx) and (pattern[1]=='1' or oky))
                    if pattern[0]=='0':errors.append((w,abs(px-x)/max(1.,abs(x))))
                    if len(cases)<3 and not ((pattern[0]=='1' or okx) and (pattern[1]=='1' or oky)):
                        cases.append({'case_id':row['case_id'],'truth':[x,y],'prediction':[float(px),float(py)]})
            score=correct/denominator
            result[tier]={'joint_5pct':score,'n':len(rows),'weighted_tasks':denominator,
                          'mean_hidden_x_relative_error':sum(w*e for w,e in errors)/sum(w for w,_ in errors) if errors else None,
                          'failures':cases}
            if tier in active and first is None and score+1e-10<gate/100:first=tier
    return {'scores':result,'first_failing':first,'gate_percent':gate,'active_tiers':list(active)}


def donor_identity(donor_path,source,teacher,ck,args,masked):
    return {'schema':SCHEMA,'donor_checkpoint':str(donor_path.resolve()),
            'donor_checkpoint_sha256':fingerprint(donor_path),'donor_step':int(ck['step']),
            'source':str(source.resolve()),'source_sha256':fingerprint(source),
            'teacher':str(teacher.resolve()),'teacher_sha256':fingerprint(teacher),
            'masked_levels':list(masked),'mix':args.xyop_mix,'seed':args.seed,
            'registers':args.registers,'sigma_steps':args.sigma_steps,
            'gaussian_weight':args.gaussian_weight,'magnitude_weight':args.magnitude_weight,
            'learning_rate':args.lr,'views':args.views,'probe_per_tier':args.probe_per_tier,
            'gate_increment':args.gate_increment,'initial_gate':args.gate_percent if args.gate_percent is not None else 5.0,
            'architecture':'trained_v3_body_sign_attention_with_gaussian_register_bank',
            'training':'new_adamw_no_old_optimizer_or_exponent_or_mantissa',
            'evaluation':'dev_hidden_XY_5pct_only'}


def run(args):
    out=args.output.expanduser().resolve()
    source=args.source.expanduser().resolve();teacher=args.teacher.expanduser().resolve()
    donor_path=(args.donor.expanduser().resolve()/'latest.pt')
    if any(out==bad or out in bad.parents or bad in out.parents for bad in (source.parent,teacher.parent,donor_path.parent)):
        raise ValueError('REGISTER_OUTPUT_OVERLAPS_DONOR_SOURCE_OR_TEACHER')
    if args.views<1 or args.chunk_steps<1 or args.registers<8 or args.sigma_steps<=0 or args.lr<=0 or args.probe_per_tier<8 or args.additional_steps<0 or args.gaussian_weight<0 or args.magnitude_weight<=0 or (args.gate_percent is not None and (not math.isfinite(args.gate_percent) or not 0<args.gate_percent<=100)) or not 0<args.gate_increment<=100:
        raise ValueError('REGISTER_BAD_CONFIGURATION')
    if not args.xyop_mix:args.xyop_mix=['*:*=011:1']
    mixes=xyop.parse_mixes(args.xyop_mix)
    latest=out/'latest.pt';config=out/'config.json'
    if latest.exists()!=config.exists():raise ValueError('REGISTER_PARTIAL_FORK_STATE')
    if latest.exists():
        ident=json.loads(config.read_text(encoding='utf8'))
        if ident['schema']!=SCHEMA or ident['source_sha256']!=fingerprint(source) or ident['teacher_sha256']!=fingerprint(teacher):
            raise ValueError('REGISTER_RESUME_PROVENANCE_DRIFT')
        # Configuration arguments affecting training cannot drift on resume.
        for name,expected in [('registers',args.registers),('sigma_steps',args.sigma_steps),
                              ('gaussian_weight',args.gaussian_weight),('magnitude_weight',args.magnitude_weight),
                              ('learning_rate',args.lr),('views',args.views),('probe_per_tier',args.probe_per_tier),
                              ('seed',args.seed),('mix',args.xyop_mix),('gate_increment',args.gate_increment)]:
            if ident[name]!=expected:raise ValueError('REGISTER_RESUME_CONFIG_DRIFT:'+name)
        fork=torch.load(latest,map_location='cpu',weights_only=True)
        if fork.get('identity')!=ident or fork.get('schema')!=SCHEMA:
            raise ValueError('REGISTER_CHECKPOINT_IDENTITY_DRIFT')
        step=int(fork['fork_step'])
        previous_mask=tuple(fork.get('masked_levels',ident['masked_levels']))
        previous_gate=float(fork['gate_percent'])
        masked,gate=effective_curriculum(args,fork,initial_gate=ident['initial_gate'])
        gate_setting=float(args.gate_percent) if args.gate_percent is not None else float(
            fork.get('gate_setting_percent',ident['initial_gate']))
        emit('register_curriculum_controls',fork_step=step,
             previous_masked_levels=list(previous_mask),masked_levels=list(masked),
             previous_gate_percent=previous_gate,gate_percent=gate,
             gate_source='cli_changed_or_reset' if gate!=previous_gate else 'checkpoint_continued',
             configured_gate_percent=gate_setting,
             mask_source='cli' if args.mask_levels is not None else 'unmasked_default',
             weights_optimizer_and_step_resumed=True)
        model=ex.load_current(source,args.device,args.dtype,args.allow_model_download,args.seed)
        model['decoder']=build_for_resume(model,fork,args.registers,args.sigma_steps)
        optimizer=torch.optim.AdamW(model['decoder'].parameters(),lr=args.lr)
        optimizer.load_state_dict(fork['optimizer'])
        torch.set_rng_state(fork['rng_cpu'])
        if args.device=='cuda' and fork.get('rng_cuda') is not None:torch.cuda.set_rng_state_all(fork['rng_cuda'])
    else:
        if args.mode=='probe':raise FileNotFoundError('REGISTER_FORK_CHECKPOINT_REQUIRED')
        ck,_=adapter.load_run_state(args.donor)
        if ck.get('xyop_mask_contract') is None:raise ValueError('REGISTER_XYOP_DONOR_REQUIRED')
        masked,gate=effective_curriculum(args)
        gate_setting=gate
        ident=donor_identity(donor_path,source,teacher,ck,args,masked)
        if ident['source_sha256']!=ck['config']['source_sha256'] or ident['teacher_sha256']!=ck['config']['teacher_sha256']:
            raise ValueError('REGISTER_DONOR_TEACHER_SOURCE_DRIFT')
        model=ex.load_current(source,args.device,args.dtype,args.allow_model_download,args.seed)
        bank,old_decoder=build_from_donor(model,ck,args.registers,args.sigma_steps)
        step=0
        optimizer=torch.optim.AdamW(bank.parameters(),lr=args.lr)
        # We evaluate the V3 donor using the exact same dev population and
        # visible Y, then discard old exponent/mantissa modules.
        cache_ident={'schema':SCHEMA+'.capture-cache','source_sha256':ident['source_sha256'],
                     'teacher_sha256':ident['teacher_sha256'],'capture_max_tokens':model['config']['max_prompt_tokens']}
        out.mkdir(parents=True,exist_ok=True)
        cache=cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',cache_ident,args.cache_memory_mb)
        active=tuple(t for t in hd.TIERS if t not in masked)
        baseline=evaluate(model,cache,teacher,active,mixes,args.seed,args.probe_per_tier,gate,decoder=old_decoder)
        model['decoder']=bank
        write_json(out/'donor-baseline.json',baseline)
        write_json(config,ident)
        state={'schema':SCHEMA,'identity':ident,'fork_step':0,'gate_percent':gate,'gate_setting_percent':gate_setting,'masked_levels':list(masked),
               'focus':active[0],'decoder':bank.state_dict(),'optimizer':optimizer.state_dict(),
               'rng_cpu':torch.get_rng_state(),'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}
        atomic_torch(latest,state)
        emit('register_bank_fork_initialized',donor_step=ident['donor_step'],
             output=str(out),parameters=sum(p.numel() for p in bank.parameters()),
             masked_levels=list(masked),registers=args.registers,donor_unchanged=True)
    active=tuple(t for t in hd.TIERS if t not in masked)
    cache_ident={'schema':SCHEMA+'.capture-cache','source_sha256':ident['source_sha256'],
                 'teacher_sha256':ident['teacher_sha256'],'capture_max_tokens':model['config']['max_prompt_tokens']}
    cache=cached.FrozenCaptureCache(out/'frozen-capture-cache-v1',cache_ident,args.cache_memory_mb)
    def probe_now():
        report=evaluate(model,cache,teacher,active,mixes,args.seed,args.probe_per_tier,gate)
        return report
    report=probe_now()
    if args.mode=='probe':
        emit('register_bank_probe',fork_step=step,donor_step=ident['donor_step'],**report)
        return
    if args.additional_steps==0:
        emit('register_bank_initialized',fork_step=step,**report)
        return
    for target in range(step+1,step+args.additional_steps+1):
        focus=report['first_failing'] or active[-1]
        model['decoder'].train();optimizer.zero_grad(set_to_none=True)
        examples=select_training_rows(teacher,focus,ident['donor_step']+target,args.seed,args.views,active)
        for view,row in enumerate(examples):
            if not rt.token_eligible(model,row['prompt']):raise ValueError('REGISTER_INELIGIBLE_TRAIN')
            pattern=xyop.choose_pattern(mixes,row,args.seed,ident['donor_step']+target,view)
            if pattern[:2]=='11':raise ValueError('REGISTER_TRAIN_BOTH_TARGETS_VISIBLE')
            channel=xyop.pack(row['x'],row['y'],pattern)
            out_xy=forward_masked(model,[row['prompt']],[channel],cache)
            truth=torch.tensor([[float(row['x']),float(row['y'])]],dtype=torch.float32,device=args.device)
            loss,parts=model['decoder'].objective(out_xy,truth,args.gaussian_weight,args.magnitude_weight)
            if not torch.isfinite(loss):raise FloatingPointError('REGISTER_NONFINITE_LOSS')
            (loss/len(examples)).backward()
        torch.nn.utils.clip_grad_norm_(model['decoder'].parameters(),1.)
        optimizer.step()
        if target%args.chunk_steps==0 or target==step+args.additional_steps:
            report=probe_now()
            while report['first_failing'] is None and gate<100:
                gate=min(100.,gate+args.gate_increment)
                # Reuse the SAME fixed dev probe; changing a gate doesn't
                # require rerunning frozen-Qwen inference.
                report['first_failing']=next((t for t in active if
                    report['scores'][t]['joint_5pct']+1e-10 < gate/100),None)
                report['gate_percent']=gate
                emit('register_gate_advanced',fork_step=target,gate_percent=gate,first_failing=report['first_failing'])
            saved={'schema':SCHEMA,'identity':ident,'fork_step':target,
                   'gate_percent':gate,'gate_setting_percent':gate_setting,'masked_levels':list(masked),
                   'focus':report['first_failing'],
                   'decoder':model['decoder'].state_dict(),'optimizer':optimizer.state_dict(),
                   'rng_cpu':torch.get_rng_state(),
                   'rng_cuda':torch.cuda.get_rng_state_all() if args.device=='cuda' else None}
            atomic_torch(latest,saved)
            write_json(out/f'probe-fork-step-{target:07d}.json',dict(report,fork_step=target,loss_parts=parts))
            emit('register_bank_probe',fork_step=target,donor_step=ident['donor_step'],
                 focus=focus,first_failing=report['first_failing'],gate_percent=gate,
                 focus_5pct=report['scores'][focus]['joint_5pct'],loss=float(loss.detach()),
                 loss_parts=parts,masked_levels=list(masked),checkpoint=str(latest))
            if gate==100 and report['first_failing'] is None:break


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('train','probe'),nargs='?',default='train')
    p.add_argument('--donor',type=Path,default=science.DEFAULT_OUT,help='Existing V3 donor RUN DIRECTORY; read-only')
    p.add_argument('--source',type=Path,default=science.DEFAULT_SOURCE)
    p.add_argument('--teacher',type=Path,default=science.DEFAULT_TEACHER)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT,help='Independent fork directory')
    p.add_argument('--xyop-mix',action='append',default=[])
    p.add_argument('--mask-levels',default=None,help='Tiers to exclude this invocation; omitted or none enables ALL tiers, even on resume')
    p.add_argument('--registers',type=int,default=512)
    p.add_argument('--sigma-steps',type=float,default=2.)
    p.add_argument('--gaussian-weight',type=float,default=.1)
    p.add_argument('--magnitude-weight',type=float,default=1.)
    p.add_argument('--gate-percent',type=float,default=None,help='Starting gate (default 5 new). Changing value on resume resets stage once; repeating same value preserves subsequent progression')
    p.add_argument('--reset-gate',action='store_true',help='Force --gate-percent stage reset even if the configured starting value is unchanged')
    p.add_argument('--gate-increment',type=float,default=5.)
    p.add_argument('--additional-steps',type=int,default=12000,help='Optimizer steps on this fork, not donor global steps')
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--views',type=int,default=4)
    p.add_argument('--probe-per-tier',type=int,default=48)
    p.add_argument('--lr',type=float,default=1e-5)
    p.add_argument('--seed',type=int,default=20261018)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    run(args)


if __name__=='__main__':main()
