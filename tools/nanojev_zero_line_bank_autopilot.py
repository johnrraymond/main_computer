#!/usr/bin/env python3
"""Resumable, fail-closed NanoJev absolute-magnitude bank growth to 2,000,000.

Start with accepted 0..127 backbone, 128..159 bank/router and repaired 160..191
bank/gate. Every later gate learns an independent HARD choice: incumbent or new
bank, using frozen operand-derived neural features only. The true sum is a
supervised training/evaluation label, NEVER an inference/routing feature.

Stage checkpoints contain optimizer and sampler state. OOD magnitude folds are
not used to train or choose checkpoints. Pre-existing domains are preserved by
measured retention, not input-magnitude overrides. Small prior bands may be
checked exhaustively; larger historical bands are bounded sampled witnesses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path

import torch
from torch.nn import functional as F

from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_absolute_magnitude_banks import (
    AbsoluteBank, atomic_json, atomic_pt, exact_mask, loss_for, score,
)
from tools.nanojev_zero_line_learned_router import (
    NeuralRouter, cache_with_tokens, hard_select, mixture_loss,
)
from tools.nanojev_zero_line_cumulative_bank import load_prior, _whole_shell

SCHEMA='nanojev.zero-line-bank-autopilot.v2'
TARGET_LIMIT=2_000_000
BASE_LIMIT=127
START=192
BOOTSTRAP_BANDS=((128,159),(160,191))
GROUPS=('zero','near','inner','frontier','mid','far')


def proposed_bands(start=START,target=TARGET_LIMIT):
    """Two banks at width 32, then two at 64, etc. No uncovered magnitudes."""
    if start<START or target<start or target>TARGET_LIMIT:
        raise ValueError('ZERO_AUTO_BAD_LIMIT')
    lo=START; width=32; result=[]
    while lo<=target:
        for _ in range(2):
            if lo>target:break
            hi=min(target,lo+width-1)
            if hi>=start:result.append([max(start,lo),hi])
            lo=hi+1
        width*=2
    return result


def fingerprint_pair(a,b,seed):
    u,v=sorted((abs(int(a)),abs(int(b))))
    h=hashlib.blake2b(f'{seed}/{u}/{v}'.encode(),digest_size=8).digest()
    return int.from_bytes(h,'little')%4


def heldout_m(m,seed):
    return (m+seed)%5==0


def fold(a,b,seed):
    m=max(abs(a),abs(b))
    if heldout_m(m,seed):return 'ood'
    return 'dev' if fingerprint_pair(a,b,seed)==0 else 'train'


def make_pair(rng,m,group,sign,orientation):
    """Training/eval dataset oracle ONLY; never a router feature.

    Zero and near cards preserve the signed cancellation relation at magnitude m.
    Far cards include ordinary same-sign addition, not just cancellation.
    """
    if group=='zero': d=0
    elif group=='near':d=sign*rng.randint(1,2)
    elif group=='inner':d=sign*rng.randint(3,15)
    elif group=='frontier':d=sign*rng.randint(16,32)
    elif group=='mid':d=sign*rng.randint(33,64)
    else: d=sign*rng.randint(65,max(65,2*m))
    # Set magnitude of one input to m while allowing b in [-m,m].
    # For small residuals the m-signed anchor must match residual sign.
    anchor=sign*m
    b=d-anchor
    if abs(b)>m:
        return None
    if orientation<0: return b,anchor
    return anchor,b


def sample_band(low,high,seed,per_cell=96,*,splits=('train','dev','ood')):
    """O(needed_samples), NOT O(high**2). Split max-magnitude and sign mirrors.

    Sampling is deterministic; TRAIN/DEV share no unordered absolute-magnitude
    families and OOD shares no maximum magnitudes with either.
    """
    if not 128<=low<=high<=TARGET_LIMIT or per_cell<1:
        raise ValueError('ZERO_AUTO_BAD_SAMPLE_RANGE')
    mag_by_fold={s:[] for s in splits}
    # Large ranges: do not materialize an integer list proportional to width.
    for name in splits:
        if name=='ood':
            first=low+((-seed-low)%5)
            count=max(0,(high-first)//5+1)
            mag_by_fold[name]=('arithmetic',first,count)
        else:
            first=low+((-seed-low)%5)
            heldout_count=max(0,(high-first)//5+1)
            mag_by_fold[name]=('non_heldout',low,high,heldout_count)
    rng=random.Random(f'zero-auto-sampling:{seed}:{low}:{high}:{per_cell}')
    result={s:[] for s in splits}
    for split in splits:
        seen=set(); attempts=0
        for group in GROUPS:
            for sign in ((1,) if group=='zero' else (-1,1)):
                for orientation in (-1,1):
                    added=0; cell_tries=0
                    while added<per_cell and cell_tries<max(2000,per_cell*150):
                        cell_tries+=1; attempts+=1
                        kind,*detail=mag_by_fold[split]
                        if kind=='arithmetic':
                            first,count=detail
                            if count==0:break
                            m=first+5*rng.randrange(count)
                        else:
                            lo,hi,_=detail
                            m=rng.randint(lo,hi)
                            if heldout_m(m,seed):continue
                        pair=make_pair(rng,m,group,sign,orientation)
                        if pair is None:continue
                        a,b=pair
                        if fold(a,b,seed)!=split:continue
                        if (a,b) in seen:continue
                        seen.add((a,b));result[split].append((a,b,a+b,0,sign))
                        added+=1
                    # Some narrow regions have too few *distinct* zero cards
                    # for 4 orientations: require representative, not exact quota.
                    if added==0:
                        raise RuntimeError(f'ZERO_AUTO_MISSING_SAMPLE_CELL {split} {group} {sign} {orientation} {low}:{high}')
        rng.shuffle(result[split])
    return result


def _rows_to_card(rows):
    # cache_with_tokens accepts tuples (a,b,delta,_,_) and uses no labels as inputs.
    return rows


def _path(s):return Path(s).expanduser().resolve()


def _check_output_safety(out, roots):
    for p in roots:
        if out==p or out in p.parents or p in out.parents:
            raise RuntimeError('ZERO_AUTO_OUTPUT_OVERLAPS_INPUT')


def _read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def _load_frozen_bank(path,band,source_hash,width,device):
    payload=torch.load(path,map_location='cpu',weights_only=True)
    if payload.get('source_sha256')!=source_hash or payload.get('band')!=list(band):
        raise RuntimeError('ZERO_AUTO_BANK_PROVENANCE_MISMATCH')
    hidden=payload.get('hidden')
    if hidden is None:
        # In the first promoted absolute bank the hidden value lives in config.
        raise RuntimeError('ZERO_AUTO_BANK_MISSING_HIDDEN')
    model=AbsoluteBank(width,int(hidden)).to(device)
    model.load_state_dict(payload['bank'],strict=True)
    model.eval().requires_grad_(False)
    return model


def _load_frozen_gate(path,band,source_hash,bank_hash,feature_width,token_width,include_tokens,device):
    payload=torch.load(path,map_location='cpu',weights_only=True)
    if (payload.get('source_sha256')!=source_hash or
        payload.get('new_band')!=list(band) or
        payload.get('new_bank_sha256')!=bank_hash or
        payload.get('feature_width')!=feature_width or
        payload.get('token_width')!=token_width or
        payload.get('include_tokens')!=include_tokens):
        raise RuntimeError('ZERO_AUTO_GATE_PROVENANCE_MISMATCH')
    model=NeuralRouter(feature_width,token_width,int(payload['hidden']),include_tokens).to(device)
    model.load_state_dict(payload['gate'],strict=True)
    model.eval().requires_grad_(False)
    return model


class FrozenChain:
    """Entirely neural inference: no target, residual or magnitude access."""
    def __init__(self,base,first_bank,first_gate,include_tokens=True):
        self.base=base.eval().requires_grad_(False)
        self.first_bank=first_bank.eval().requires_grad_(False)
        self.first_gate=first_gate.eval().requires_grad_(False)
        self.include_tokens=include_tokens
        self.links=[]

    def append(self,band,bank,gate):
        self.links.append((tuple(band),bank.eval().requires_grad_(False),gate.eval().requires_grad_(False)))

    @torch.inference_mode()
    def existing(self,cache,chunk=2048):
        output=[]; counts=[]
        for i in range(0,len(cache['features']),chunk):
            f=cache['features'][i:i+chunk];t=cache['tokens'][i:i+chunk]
            out,route=hard_select(self.first_gate(f,t),cache['original'][i:i+chunk],self.first_bank(f))
            count=route.long()
            for _,bank,gate in self.links:
                out,d=hard_select(gate(f,t),out,bank(f))
                count+=d.long()
            output.append(out);counts.append(count)
        return torch.cat(output),torch.cat(counts)


def prep(chain,rows,device,batch):
    x=cache_with_tokens(chain.base,_rows_to_card(rows),device,batch,chain.include_tokens)
    incumbent,routecount=chain.existing(x,batch)
    x['incumbent']=incumbent
    x['incumbent_route_count']=routecount
    return x


@torch.inference_mode()
def eval_gate(gate,cache,chunk=2048):
    result=[];routes=[]
    for i in range(0,len(cache['target']),chunk):
        f=cache['features'][i:i+chunk];t=cache['tokens'][i:i+chunk]
        out,selected=hard_select(gate(f,t),cache['incumbent'][i:i+chunk],cache['new_logits'][i:i+chunk])
        result.append(out);routes.append(selected)
    combined=torch.cat(result);choice=torch.cat(routes)
    old=exact_mask(cache['incumbent'],cache['target']);new=exact_mask(combined,cache['target'])
    near=cache['target'].abs()<=2
    zero=cache['target']==0
    nonzero_near=(cache['target'].abs()>=1)&(cache['target'].abs()<=2)
    return {'cards':len(old),'accuracy':float(new.float().mean()),
            'incumbent_accuracy':float(old.float().mean()),
            'new_harm':int((old&~new).sum()),'new_fixes':int((~old&new).sum()),
            'near_zero_harm':int((near&old&~new).sum()),
            'zero_accuracy':float(new[zero].float().mean()) if bool(zero.any()) else None,
            'near_accuracy':float(new[nonzero_near].float().mean()) if bool(nonzero_near.any()) else None,
            'new_bank_selection':float(choice.float().mean())}


@torch.inference_mode()
def compare_experts(cache):
    """Offline diagnostic ONLY. True targets never reach router features/forward.

    The per-card union is an answer-aware ceiling, not an inference policy.
    Compute all metrics on the very same cards (including fixes and regressions).
    """
    incumbent=exact_mask(cache['incumbent'],cache['target'])
    specialist=exact_mask(cache['new_logits'],cache['target'])
    either=incumbent|specialist
    both=incumbent&specialist
    n=int(incumbent.numel())
    if not n:raise RuntimeError('ZERO_AUTO_EMPTY_EXPERT_COMPARISON')
    return {
        'cards':n,
        'incumbent_accuracy':float(incumbent.float().mean()),
        'specialist_accuracy':float(specialist.float().mean()),
        'offline_answer_aware_ceiling':float(either.float().mean()),
        'both_correct':int(both.sum()),
        'incumbent_only_correct':int((incumbent&~specialist).sum()),
        'specialist_only_correct':int((~incumbent&specialist).sum()),
        'both_wrong':int((~either).sum()),
        'net_specialist_gain':int(specialist.sum()-incumbent.sum()),
        'note':'Offline answer-aware OR upper bound; NEVER an inference route',
    }


def adaptive_bank_stage(bank,train,dev,*,stage,meta,out,args,device):
    """Bounded, DEV-only extension: never train or select from OOD.

    Fixed probe checkpoints remain fully optimizer/RNG resumable. A plateau
    compares best DEV before/after each extension block, not training loss.
    """
    milestones=[]; steps=args.bank_steps; stagnant=0; previous=None
    # Recover existing block metadata. train_bank_stage reloads its own optimizer.
    progress=out/'adaptive_progress.json'
    if progress.is_file():
        recovered=_read(progress)
        if recovered.get('training_meta')!=meta:
            raise RuntimeError('ZERO_AUTO_ADAPTIVE_RESUME_MISMATCH')
        milestones=list(recovered['milestones'])
        if milestones:
            steps=int(milestones[-1]['step'])
            stagnant=int(milestones[-1]['stagnant'])
            previous=milestones[-1]['best_dev']
            if recovered.get('complete'):
                best_pt=torch.load(out/'bank_best.pt',map_location=device,weights_only=True)
                bank.load_state_dict(best_pt['bank'])
                bank.eval().requires_grad_(False)
                return best_pt['step'],float(previous),milestones
    if (out/'bank_training.pt').is_file():
        saved=torch.load(out/'bank_training.pt',map_location='cpu',weights_only=True)
        if saved['step']>steps:
            steps=int(saved['step'])
    while True:
        bank.requires_grad_(True)
        best_step,best=train_bank_stage(
            bank,train,dev,stage=stage,meta=meta,out=out,steps=steps,
            probe_every=args.bank_probe_every,batch_size=args.batch_size,
            lr=args.bank_lr,seed=args.seed+stage[0],device=device)
        gain=(best-previous) if previous is not None else None
        if gain is not None:
            stagnant=stagnant+1 if gain<args.bank_min_dev_gain else 0
        reason=('target_reached' if best>=args.bank_target_accuracy else
                'max_steps' if steps>=args.bank_max_steps else
                'dev_plateau' if stagnant>=args.bank_patience else None)
        milestone={'step':steps,'best_step':best_step,'best_dev':best,
                   'dev_gain':gain,'stagnant':stagnant,'stop_reason':reason}
        # If the process stopped after training but before progress JSON was
        # saved, avoid duplicate milestone and resume optimizer normally.
        milestones=[m for m in milestones if m['step']<steps]+[milestone]
        atomic_json(progress,{'schema':SCHEMA+'.adaptive-progress',
                              'training_meta':meta,'milestones':milestones,
                              'complete':bool(reason)})
        print('ZERO_AUTO_BANK_ADAPT='+json.dumps(
            {'band':stage,'steps':steps,'best_dev':round(best,6),
             'gain':None if gain is None else round(gain,6),
             'stop_reason':reason},sort_keys=True),flush=True)
        if reason:return best_step,best,milestones
        previous=best
        steps=min(args.bank_max_steps,steps+args.bank_extension_steps)


def optional_warmstart_bank(bank,args,band,source_hash,stage):
    """Read previous best bank weights, never previous optimizer or OOD labels."""
    if not args.bank_warmstart_stage or (stage/'bank_training.pt').exists():
        return None
    old=_path(args.bank_warmstart_stage)
    cfg=_read(old/'config.json')
    if cfg.get('band')!=list(band):
        return None  # A warmstart from the first expansion must not seed later banks.
    if cfg.get('source_sha256')!=source_hash:
        raise RuntimeError('ZERO_AUTO_WARMSTART_PROVENANCE_MISMATCH')
    if cfg.get('training_meta',{}).get('bank_hidden')!=args.bank_hidden:
        raise RuntimeError('ZERO_AUTO_WARMSTART_WIDTH_MISMATCH')
    weight=old/'bank_best.pt'
    payload=torch.load(weight,map_location='cpu',weights_only=True)
    bank.load_state_dict(payload['bank'],strict=True)
    record={'source':str(weight),'sha256':sha256(weight),
            'prior_selected_step':int(payload['step']),
            'optimizer':'fresh; weights-only warm start'}
    atomic_json(stage/'warmstart.json',record)
    return record

def choose_sample(rng,cache,n,subset=None):
    length=len(cache['target']) if subset is None else len(subset)
    if not length:raise RuntimeError('ZERO_AUTO_EMPTY_TRAINING_POOL')
    ii=torch.tensor([rng.randrange(length) for _ in range(n)],device=cache['target'].device)
    return ii if subset is None else subset[ii]


def _training_meta(args):
    return {'seed':args.seed,'sample_per_cell':args.sample_per_cell,
            'bank_steps':args.bank_steps,'gate_steps':args.gate_steps,
            'bank_lr':args.bank_lr,'gate_lr':args.gate_lr,'bank_hidden':args.bank_hidden,
            'gate_hidden':args.gate_hidden,'batch_size':args.batch_size,
            'full_legacy_retention':args.full_legacy_retention,
            'history_samples':args.history_samples,
            'min_accuracy':args.min_accuracy,
            'bank_target_accuracy':args.bank_target_accuracy,
            'bank_max_steps':args.bank_max_steps,
            'bank_extension_steps':args.bank_extension_steps,
            'bank_patience':args.bank_patience,
            'bank_min_dev_gain':args.bank_min_dev_gain,
            'bank_warmstart_stage':str(_path(args.bank_warmstart_stage)) if args.bank_warmstart_stage else '',
            'bank_warmstart_sha256':sha256(_path(args.bank_warmstart_stage)/'bank_best.pt') if args.bank_warmstart_stage else '',
            'gate_patience':args.gate_patience,'gate_min_dev_gain':args.gate_min_dev_gain,
            'min_zero_accuracy':args.min_zero_accuracy,'min_near_accuracy':args.min_near_accuracy}


def _assert_meta(state,meta):
    if state.get('training_meta')!=meta:
        raise RuntimeError('ZERO_AUTO_RESUME_CONFIG_MISMATCH')


def train_bank_stage(bank,train,dev,*,stage,meta,out,steps,probe_every,batch_size,lr,seed,device):
    """Optimizer+RNG resumable; DEV selects best. Never touches OOD."""
    outfile=out/'bank_training.pt';selected=out/'bank_best.pt'
    opt=torch.optim.AdamW(bank.parameters(),lr=lr,weight_decay=0.)
    rng=random.Random(seed)
    step0=0;best=-1.;best_step=0
    if outfile.is_file():
        s=torch.load(outfile,map_location=device,weights_only=True)
        _assert_meta(s,meta)
        bank.load_state_dict(s['bank']);opt.load_state_dict(s['optimizer'])
        rng.setstate(s['rng']);step0=s['step'];best=s['best'];best_step=s['best_step']
    groups={g:[] for g in GROUPS}
    ys=train['target']
    for index,y in enumerate(ys.tolist()):
        ab=abs(y)
        g=('zero' if ab==0 else 'near' if ab<=2 else 'inner' if ab<=15
           else 'frontier' if ab<=32 else 'mid' if ab<=64 else 'far')
        groups[g].append(index)
    if any(not z for z in groups.values()):raise RuntimeError('ZERO_AUTO_EMPTY_TRAIN_GROUP')
    weighted=('zero','near','near','inner','frontier','frontier','mid','far','far')
    for step in range(step0+1,steps+1):
        indices=torch.tensor([rng.choice(groups[rng.choice(weighted)]) for _ in range(batch_size)],device=device)
        bank.train();logits=bank(train['features'][indices])
        loss=loss_for(logits,train['target'][indices])
        opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(bank.parameters(),1.);opt.step()
        if step%probe_every and step!=steps:continue
        bank.eval()
        with torch.no_grad():
            dv=score(bank(dev['features']),dev)['accuracy']
        if dv>best:
            best=dv;best_step=step
            atomic_pt(selected,{'training_meta':meta,'step':step,'bank':{k:v.detach().cpu().clone() for k,v in bank.state_dict().items()}})
        atomic_pt(outfile,{'training_meta':meta,'step':step,'bank':bank.state_dict(),
                            'optimizer':opt.state_dict(),'rng':rng.getstate(),
                            'best':best,'best_step':best_step})
        print(f'ZERO_AUTO_BANK band={stage[0]}:{stage[1]} step={step}/{steps} DEV={dv:.5f} best={best:.5f}',flush=True)
    if not selected.is_file():raise RuntimeError('ZERO_AUTO_BANK_BEST_MISSING')
    bank.load_state_dict(torch.load(selected,map_location=device,weights_only=True)['bank'])
    bank.eval().requires_grad_(False)
    return best_step,best


def train_gate_stage(gate,center,replay,new_train,new_dev,historical,*,stage,meta,out,
                     steps,probe_every,batch_size,lr,seed,device,margin=1.5,
                     patience=0,min_dev_gain=.001):
    """Learn neural choice from final-answer likelihood; penalize harmful TRAIN replay."""
    outfile=out/'gate_training.pt';selected=out/'gate_best.pt'
    opt=torch.optim.AdamW(gate.parameters(),lr=lr,weight_decay=0.)
    rng=random.Random(seed);step0=0;best_quality=None;best_step=0
    stagnant=0;previous_safe_best=-1.;stopped=False
    if outfile.is_file():
        s=torch.load(outfile,map_location=device,weights_only=True)
        _assert_meta(s,meta)
        gate.load_state_dict(s['gate']);opt.load_state_dict(s['optimizer'])
        rng.setstate(s['rng']);step0=s['step'];best_quality=s['quality'];best_step=s['best_step']
        stagnant=s.get('stagnant',0);previous_safe_best=s.get('previous_safe_best',-1.)
        stopped=s.get('stopped',False)
    pools=(center,replay,new_train)
    for step in range(step0+1 if not stopped else steps+1,steps+1):
        ns=(batch_size//4,batch_size//4,batch_size-batch_size//2)
        sampled=[choose_sample(rng,p,n) for p,n in zip(pools,ns)]
        batch=lambda k:torch.cat([p[k][idx] for p,idx in zip(pools,sampled)],dim=0)
        gate.train();logits=gate(batch('features'),batch('tokens'))
        loss=mixture_loss(logits,batch('incumbent'),batch('new_logits'),batch('target'))
        # Only known TRAIN replay may penalize harmful switches. No test oracle.
        old_part=torch.cat((center['target'][sampled[0]],replay['target'][sampled[1]]))
        old_inc=torch.cat((center['incumbent'][sampled[0]],replay['incumbent'][sampled[1]]))
        old_new=torch.cat((center['new_logits'][sampled[0]],replay['new_logits'][sampled[1]]))
        avoid=exact_mask(old_inc,old_part)&~exact_mask(old_new,old_part)
        if bool(avoid.any()):loss=loss+F.softplus(logits[:len(old_part)][avoid]+margin).mean()
        opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(gate.parameters(),1.);opt.step()
        if step%probe_every and step!=steps:continue
        gate.eval();center_v=eval_gate(gate,center);old_v=eval_gate(gate,historical)
        new_v=eval_gate(gate,new_dev)
        is_safe=center_v['new_harm']==0 and old_v['new_harm']==0
        quality=(int(is_safe),-center_v['new_harm']-old_v['new_harm'],
                 new_v['accuracy'],new_v['new_fixes']-new_v['new_harm'])
        if best_quality is None or quality>tuple(best_quality):
            best_quality=quality;best_step=step
            atomic_pt(selected,{'training_meta':meta,'step':step,
                       'gate':{k:v.detach().cpu().clone() for k,v in gate.state_dict().items()}})
        if is_safe and new_v['accuracy']>previous_safe_best+min_dev_gain:
            previous_safe_best=new_v['accuracy'];stagnant=0
        elif is_safe:
            stagnant+=1
        # Unsafe checkpoints never establish successful plateau evidence.
        stopped=bool(patience and stagnant>=patience)
        atomic_pt(outfile,{'training_meta':meta,'step':step,'gate':gate.state_dict(),
                            'optimizer':opt.state_dict(),'rng':rng.getstate(),
                            'quality':best_quality,'best_step':best_step,
                            'stagnant':stagnant,'previous_safe_best':previous_safe_best,
                            'stopped':stopped})
        print(f'ZERO_AUTO_GATE band={stage[0]}:{stage[1]} step={step}/{steps} '
              f'DEV={new_v["accuracy"]:.5f} CENTER_HARM={center_v["new_harm"]} '
              f'HISTORY_HARM={old_v["new_harm"]} ROUTE={new_v["new_bank_selection"]:.4f} '
              f'PATIENCE={stagnant}/{patience}',flush=True)
        if stopped:
            print(f'ZERO_AUTO_GATE_PLATEAU band={stage[0]}:{stage[1]} step={step}',flush=True)
            break
    if not selected.is_file():raise RuntimeError('ZERO_AUTO_GATE_BEST_MISSING')
    gate.load_state_dict(torch.load(selected,map_location=device,weights_only=True)['gate'])
    gate.eval().requires_grad_(False)
    return best_step


def certification_decision(metrics,args):
    """Final evidence gate. Learned selection rate is *not* a quota.

    A hard neural route must improve arithmetic on DEV, not reduce OOD
    arithmetic, satisfy absolute exact-zero/near-zero guarantees and preserve
    all established correct predictions on the checked historical population.
    """
    safe=metrics['center']['new_harm']==0 and metrics['history']['new_harm']==0
    net_improvement=(metrics['new_dev']['new_fixes']>metrics['new_dev']['new_harm'] and
                     metrics['new_ood']['new_fixes']>=metrics['new_ood']['new_harm'])
    accurate=all(metrics[k]['accuracy']>=args.min_accuracy and
                 metrics[k]['zero_accuracy'] is not None and
                 metrics[k]['zero_accuracy']>=args.min_zero_accuracy and
                 metrics[k]['near_accuracy'] is not None and
                 metrics[k]['near_accuracy']>=args.min_near_accuracy and
                 metrics[k]['near_zero_harm']==0 for k in ('new_dev','new_ood'))
    return bool(safe and net_improvement and accurate), {
        'retention_safe':bool(safe),'dev_gain_and_ood_nondegradation':bool(net_improvement),
        'accuracy_and_zero_line_gates':bool(accurate),
        'bank_selection_is_diagnostic_not_required':True}


def _bank_checkpoint(band,source_hash,hidden,model):
    return {'schema':SCHEMA+'.bank-promoted','source_sha256':source_hash,
            'band':list(band),'hidden':hidden,
            'bank':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()}}


def _gate_checkpoint(band,source_hash,bank_sha,feature_width,token_width,hidden,include_tokens,model):
    return {'schema':SCHEMA+'.gate-promoted','source_sha256':source_hash,
            'new_band':list(band),'new_bank_sha256':bank_sha,'feature_width':feature_width,
            'token_width':token_width,'hidden':hidden,'include_tokens':include_tokens,
            'gate':{k:v.detach().cpu().clone() for k,v in model.state_dict().items()}}


def _legacy_rows(band):
    low,high=band
    return _whole_shell(low,high)


def _historical_rows(manifest,args):
    """Return historical certification witnesses and separate TRAIN replay rows."""
    historical=[];replay=[];details=[]
    for b in manifest['bands']:
        low,high=b['band']
        legacy=(low,high) in BOOTSTRAP_BANDS
        if legacy and args.full_legacy_retention:
            witness=_legacy_rows((low,high))
            # Replay examples are independent from full historical witnesses.
            old_samples=sample_band(low,high,args.seed,args.history_samples,
                                    splits=('train',))['train']
            mode='exhaustive_legacy'
        else:
            sample=sample_band(low,high,args.seed,args.history_samples,
                               splits=('train','dev','ood'))
            witness=sample['dev']+sample['ood'];old_samples=sample['train']
            mode='fixed_sampled_witness'
        historical.append(witness);replay.extend(old_samples)
        details.append({'band':[low,high],'mode':mode,'cards':len(witness) if not isinstance(witness,dict) else len(witness['y'])})
    if not historical:raise RuntimeError('ZERO_AUTO_NO_HISTORICAL_BANDS')
    # Combine without forming a gigantic Cartesian product.
    x=[];y=[]
    for r in historical:
        if isinstance(r,dict):
            x.extend(r['x'].tolist());y.extend(r['y'].tolist())
        else:
            x.extend((a,b) for a,b,*_ in r);y.extend(a+b for a,b,*_ in r)
    historical_rows={'x':torch.tensor(x,dtype=torch.long),'y':torch.tensor(y,dtype=torch.long)}
    return historical_rows,replay,details


def _bootstrap(args,base,source):
    from tools.nanojev_zero_line_cumulative_bank import load_prior
    first,first_gate,include_tokens,bank_path,gate_path,_=load_prior(
        source,base,_path(args.prior_bank_output),_path(args.prior_router_output),args.device)
    repair=_path(args.repair_output)
    result=_read(repair/'result.json')
    if result.get('schema')!='nanojev.zero-line-cumulative-gate-repair.v1' or not result.get('eligible'):
        raise RuntimeError('ZERO_AUTO_REPAIR_NOT_CERTIFIED')
    bp=repair/'bank_promoted.pt';gp=repair/'gate_promoted.pt'
    if not bp.is_file() or not gp.is_file():raise RuntimeError('ZERO_AUTO_REPAIRED_PROMOTION_MISSING')
    second=_load_frozen_bank(bp,(160,191),source['checkpoint_sha256'],int(base.query.shape[0]),args.device)
    bank_hash=sha256(bp)
    blob=torch.load(gp,map_location='cpu',weights_only=True)
    if blob.get('prior_bank_sha256')!=sha256(bank_path) or blob.get('prior_router_sha256')!=sha256(gate_path):
        raise RuntimeError('ZERO_AUTO_PRIOR_LINK_MISMATCH')
    feature_width=int(base.query.shape[0]); token_width=14*feature_width if include_tokens else 0
    gate=_load_frozen_gate(gp,(160,191),source['checkpoint_sha256'],bank_hash,
                           feature_width,token_width,include_tokens,args.device)
    chain=FrozenChain(base,first,first_gate,include_tokens)
    chain.append((160,191),second,gate)
    refs=[{'band':[128,159],'bank_path':str(bank_path),'bank_sha256':sha256(bank_path),
           'gate_path':str(gate_path),'gate_sha256':sha256(gate_path),'kind':'original_two_way'},
          {'band':[160,191],'bank_path':str(bp),'bank_sha256':bank_hash,
           'gate_path':str(gp),'gate_sha256':sha256(gp),'kind':'repaired_cumulative'}]
    return chain,refs


def _load_chain(args,manifest,base,source):
    chain,bootstrap=_bootstrap(args,base,source)
    if manifest['bands'][:2]!=bootstrap:raise RuntimeError('ZERO_AUTO_BOOTSTRAP_PROVENANCE_CHANGED')
    width=int(base.query.shape[0]); token_width=14*width if chain.include_tokens else 0
    for b in manifest['bands'][2:]:
        bp=_path(b['bank_path']);gp=_path(b['gate_path'])
        if sha256(bp)!=b['bank_sha256'] or sha256(gp)!=b['gate_sha256']:
            raise RuntimeError('ZERO_AUTO_PROMOTED_HASH_MISMATCH')
        bank=_load_frozen_bank(bp,b['band'],source['checkpoint_sha256'],width,args.device)
        gate=_load_frozen_gate(gp,b['band'],source['checkpoint_sha256'],b['bank_sha256'],width,token_width,
                               chain.include_tokens,args.device)
        chain.append(b['band'],bank,gate)
    return chain


def _validate_plan(plan,manifest):
    previous=manifest['bands'][-1]['band'][1]
    for b in plan:
        if b[0]!=previous+1 or b[0]>b[1]:
            raise RuntimeError('ZERO_AUTO_NONCONTIGUOUS_PLAN')
        previous=b[1]


def _new_stage(args,chain,source,manifest,band,out):
    stage_id=f'{band[0]}_{band[1]}'
    stage=out/'stages'/stage_id;stage.mkdir(parents=True,exist_ok=True)
    stage_cfg=stage/'config.json'
    meta=_training_meta(args)
    if stage_cfg.exists():
        c=_read(stage_cfg)
        if c['band']!=band or c['training_meta']!=meta or c['source_sha256']!=source['checkpoint_sha256']:
            raise RuntimeError('ZERO_AUTO_STAGE_CONFIGURATION_CHANGED')
    else:
        atomic_json(stage_cfg,{'schema':SCHEMA+'.stage','band':band,'training_meta':meta,
                               'source_sha256':source['checkpoint_sha256']})
    print(f'ZERO_AUTO_STAGE_BEGIN band={band[0]}:{band[1]}',flush=True)
    splits=sample_band(*band,args.seed,args.sample_per_cell)
    train=prep(chain,splits['train'],args.device,args.eval_batch_size)
    dev=prep(chain,splits['dev'],args.device,args.eval_batch_size)
    # Fixed OOD probes only evaluated after DEV-selected checkpoints are frozen.
    old_rows,replay_rows,history_description=_historical_rows(manifest,args)
    center=prep(chain,deck(BASE_LIMIT),args.device,args.eval_batch_size)
    historic=prep(chain,old_rows,args.device,args.eval_batch_size)
    replay=prep(chain,replay_rows,args.device,args.eval_batch_size)
    if not bool(exact_mask(center['incumbent'],center['target']).all()):
        raise RuntimeError('ZERO_AUTO_BASE_NOT_PRESERVED_BEFORE_STAGE')
    bank=AbsoluteBank(int(chain.base.query.shape[0]),args.bank_hidden).to(args.device)
    warm=optional_warmstart_bank(bank,args,band,source['checkpoint_sha256'],stage)
    bank_selected,bank_dev,adaptation=adaptive_bank_stage(
        bank,train,dev,stage=band,meta=meta,out=stage,args=args,device=args.device)
    bank.eval().requires_grad_(False)
    for c in (train,dev,center,historic,replay):
        with torch.inference_mode():c['new_logits']=bank(c['features']).detach()
    expert_dev=compare_experts(dev)
    print('ZERO_AUTO_EXPERT_DIAGNOSTIC='+json.dumps(
        {'band':band,'split':'dev',**expert_dev},sort_keys=True),flush=True)
    # A perfect answer-aware choice still cannot achieve the DEV gate?
    # Do not waste gate-training compute. Do not inspect OOD to decide.
    if expert_dev['offline_answer_aware_ceiling']+1.e-9<args.min_accuracy:
        ood=prep(chain,splits['ood'],args.device,args.eval_batch_size)
        with torch.inference_mode():ood['new_logits']=bank(ood['features']).detach()
        diagnostic={'dev':expert_dev,'ood':compare_experts(ood)}
        report={'schema':SCHEMA+'.stage-report','band':band,'accepted':False,
                'source_sha256':source['checkpoint_sha256'],
                'selected_bank_step':bank_selected,'selected_gate_step':None,
                'adaptive_training':adaptation,'warmstart':warm,
                'expert_comparison':diagnostic,'metrics':{'specialist_dev':score(dev['new_logits'],dev),
                           'specialist_ood':score(ood['new_logits'],ood)},
                'historical_witnesses':history_description,
                'refusal_reason':'dev_answer_aware_ceiling_below_required_accuracy',
                'inference':'no_new_gate_promoted',
                'holdout_note':'OOD assessed once after DEV-only decisions'}
        atomic_json(stage/'result.json',report)
        print('ZERO_AUTO_GATE_SKIPPED='+json.dumps({'band':band,
            'offline_dev_ceiling':expert_dev['offline_answer_aware_ceiling'],
            'required':args.min_accuracy},sort_keys=True),flush=True)
        print('ZERO_AUTO_STAGE_REFUSED='+json.dumps(
            {'band':band,'reason':report['refusal_reason']},sort_keys=True),flush=True)
        return None,report
    gate=NeuralRouter(center['features'].shape[1],center['tokens'].shape[1],
                      args.gate_hidden,chain.include_tokens).to(args.device)
    with torch.no_grad():gate.net[-1].bias.fill_(-3.)
    gate_step=train_gate_stage(gate,center,replay,train,dev,historic,stage=band,meta=meta,
                               out=stage,steps=args.gate_steps,probe_every=args.gate_probe_every,
                               batch_size=args.batch_size,lr=args.gate_lr,seed=args.seed+band[1],device=args.device,
                               patience=args.gate_patience,min_dev_gain=args.gate_min_dev_gain)
    # Assess once, after DEV selection; never use OOD to select state/optimizer.
    ood=prep(chain,splits['ood'],args.device,args.eval_batch_size)
    with torch.inference_mode():ood['new_logits']=bank(ood['features']).detach()
    md={'center':eval_gate(gate,center),'history':eval_gate(gate,historic),
        'new_dev':eval_gate(gate,dev),'new_ood':eval_gate(gate,ood),
        'specialist_dev':score(dev['new_logits'],dev),
        'specialist_ood':score(ood['new_logits'],ood)}
    if band[1]<args.target:
        future=[band[1]+1,min(args.target,band[1]+(band[1]-band[0]+1))]
        future_rows=sample_band(*future,args.seed,args.sample_per_cell,splits=('ood',))['ood']
        diagnostic=prep(chain,future_rows,args.device,args.eval_batch_size)
        with torch.inference_mode():diagnostic['new_logits']=bank(diagnostic['features']).detach()
        md['adjacent_untrained_diagnostic']=eval_gate(gate,diagnostic)
        md['adjacent_untrained_diagnostic']['isolated_bank_accuracy']=score(diagnostic['new_logits'],diagnostic)['accuracy']
        md['adjacent_untrained_diagnostic']['range']=future
    expert_ood=compare_experts(ood)
    expert_reference={'dev':expert_dev,'ood':expert_ood}
    qualifies,criteria=certification_decision(md,args)
    if qualifies:refusal_reason=None
    elif not criteria['retention_safe']:refusal_reason='historical_retention_regression'
    elif not criteria['dev_gain_and_ood_nondegradation']:refusal_reason='no_new_arithmetic_gain'
    elif expert_ood['offline_answer_aware_ceiling']<args.min_accuracy:
        refusal_reason='ood_expert_ceiling_below_required_accuracy'
    elif md['new_dev']['accuracy']<args.min_accuracy or md['new_ood']['accuracy']<args.min_accuracy:
        refusal_reason='neural_router_or_expert_accuracy_insufficient'
    else:refusal_reason='zero_line_accuracy_insufficient'
    report={'schema':SCHEMA+'.stage-report','band':band,'accepted':bool(qualifies),
            'refusal_reason':refusal_reason,
            'source_sha256':source['checkpoint_sha256'],
            'selected_bank_step':torch.load(stage/'bank_best.pt',map_location='cpu',weights_only=True)['step'],
            'selected_gate_step':gate_step,'metrics':md,'historical_witnesses':history_description,
            'adaptive_training':adaptation,'warmstart':warm,
            'expert_comparison':expert_reference,
            'certification_checks':criteria,
            'old_regions_exhaustive':all(x['mode']=='exhaustive_legacy' for x in history_description),
            'inference':'learned_hard_gates_no_answer_or_magnitude_routing',
            'holdout_note':'OOD magnitude values never used in training or DEV checkpoint selection; adjacent-band results are diagnostic only'}
    atomic_json(stage/'result.json',report)
    if not qualifies:
        print('ZERO_AUTO_STAGE_REFUSED='+json.dumps({'band':band,'new_dev':md['new_dev']['accuracy'],
              'new_ood':md['new_ood']['accuracy'],'center_harm':md['center']['new_harm'],
              'historical_harm':md['history']['new_harm'],
              'expert_dev_ceiling':expert_dev['offline_answer_aware_ceiling'],
              'integrated_dev_gain':md['new_dev']['new_fixes']-md['new_dev']['new_harm'],
              'gate_new_bank_selection':md['new_dev']['new_bank_selection'],
              'reason':refusal_reason},sort_keys=True),flush=True)
        return None,report
    bank_pt=stage/'bank_promoted.pt';gate_pt=stage/'gate_promoted.pt'
    atomic_pt(bank_pt,_bank_checkpoint(band,source['checkpoint_sha256'],args.bank_hidden,bank))
    atomic_pt(gate_pt,_gate_checkpoint(band,source['checkpoint_sha256'],sha256(bank_pt),
             center['features'].shape[1],center['tokens'].shape[1],
             args.gate_hidden,chain.include_tokens,gate))
    link={'band':band,'bank_path':str(bank_pt),'bank_sha256':sha256(bank_pt),
          'gate_path':str(gate_pt),'gate_sha256':sha256(gate_pt),'kind':'autopilot'}
    print('ZERO_AUTO_STAGE_ACCEPTED='+json.dumps({'band':band,'new_ood':md['new_ood']['accuracy'],
          'history_new_errors':0},sort_keys=True),flush=True)
    return link,report


def initialize(args,out,source,refs):
    plan=proposed_bands(START,args.target)
    cfg={'schema':SCHEMA,'source_sha256':source['checkpoint_sha256'],
         'target':args.target,'training_meta':_training_meta(args),
         'root_paths':{'ladder':str(_path(args.ladder_output)),
                       'prior_bank':str(_path(args.prior_bank_output)),
                       'prior_router':str(_path(args.prior_router_output)),
                       'repair':str(_path(args.repair_output))}}
    atomic_json(out/'config.json',cfg)
    state={'schema':SCHEMA,'status':'ready','target':args.target,
           'bands':refs,'pending':plan,'completed_new':0,'last_result':None,
           'note':'Sampled historical retention after original finite decks; no full-square certification at 2m'}
    atomic_json(out/'manifest.json',state)
    return state


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('ZERO_AUTO_CUDA_UNAVAILABLE')
    if (args.target<START or args.target>TARGET_LIMIT or min(args.bank_steps,args.gate_steps,
        args.bank_probe_every,args.gate_probe_every,args.batch_size,args.eval_batch_size,
        args.sample_per_cell,args.history_samples,args.bank_hidden,args.gate_hidden,args.cpu_threads,
        args.bank_extension_steps,args.bank_patience,args.bank_max_steps,args.gate_patience)<1):
        raise ValueError('ZERO_AUTO_INVALID_OPTIONS')
    if (args.batch_size<4 or args.min_width<16 or args.target>9_999_999//2
        or args.bank_max_steps<args.bank_steps or args.bank_min_dev_gain<0 or args.gate_min_dev_gain<0
        or any(not (0<=v<=1) for v in (args.min_accuracy,args.bank_target_accuracy,
                                       args.min_zero_accuracy,args.min_near_accuracy))):
        raise ValueError('ZERO_AUTO_UNSUPPORTED_RANGE')
    roots=[_path(x) for x in (args.ladder_output,args.prior_bank_output,args.prior_router_output,args.repair_output)]
    out=_path(args.output);_check_output_safety(
        out,roots+([_path(args.bank_warmstart_stage)] if args.bank_warmstart_stage else []))
    torch.set_num_threads(args.cpu_threads);torch.manual_seed(args.seed)
    base,source=load_certified_model(roots[0],args.device)
    chain,refs=_bootstrap(args,base,source)
    out.mkdir(parents=True,exist_ok=True)
    conf=out/'config.json';mpath=out/'manifest.json'
    if conf.exists() != mpath.exists():raise RuntimeError('ZERO_AUTO_INCOMPLETE_MANIFEST')
    if mpath.is_file():
        old=_read(conf)
        if (old['source_sha256']!=source['checkpoint_sha256'] or old['target']!=args.target or
            old['training_meta']!=_training_meta(args) or
            old['root_paths']!={'ladder':str(roots[0]),'prior_bank':str(roots[1]),
                               'prior_router':str(roots[2]),'repair':str(roots[3])}):
            raise RuntimeError('ZERO_AUTO_RESUME_SETTINGS_OR_SOURCE_CHANGED')
        state=_read(mpath)
    else:state=initialize(args,out,source,refs)
    chain=_load_chain(args,state,base,source)
    _validate_plan(state['pending'],state)
    hashes={str(p):sha256(p) for p in [source['checkpoint'],*[_path(b[k]) for b in state['bands'] for k in ('bank_path','gate_path')],
             *([_path(args.bank_warmstart_stage)/'bank_best.pt'] if args.bank_warmstart_stage else [])]}
    advanced_this_invocation=0
    while state['pending'] and advanced_this_invocation<args.max_stages:
        band=state['pending'][0]
        link,report=_new_stage(args,chain,source,state,band,out)
        for name,digest in hashes.items():
            if sha256(name)!=digest:raise RuntimeError('ZERO_AUTO_FROZEN_SOURCE_MUTATED')
        if link is None:
            # Only inadequate experts justify narrowing the numerical interval.
            # A router/retention failure needs gate repair, not arbitrary splits.
            can_split=report.get('refusal_reason') in (
                'dev_answer_aware_ceiling_below_required_accuracy',
                'ood_expert_ceiling_below_required_accuracy')
            if args.failure_policy=='split' and can_split and band[1]-band[0]+1>=2*args.min_width:
                mid=(band[0]+band[1])//2
                state['pending']=[[band[0],mid],[mid+1,band[1]],*state['pending'][1:]]
                state['status']='split_after_refusal'
                state['last_result']={'band':band,'action':'split','report':str(out/'stages'/f'{band[0]}_{band[1]}'/'result.json')}
                atomic_json(mpath,state)
                continue
            state['status']='halted_on_refusal'
            state['last_result']={'band':band,'action':'halted','report':str(out/'stages'/f'{band[0]}_{band[1]}'/'result.json')}
            atomic_json(mpath,state)
            break
        state['bands'].append(link)
        state['pending']=state['pending'][1:]
        state['completed_new']+=1
        advanced_this_invocation+=1
        state['status']='advanced'
        state['last_result']={'band':band,'action':'promoted'}
        atomic_json(mpath,state)
        hashes[str(_path(link['bank_path']))]=link['bank_sha256']
        hashes[str(_path(link['gate_path']))]=link['gate_sha256']
        width=int(base.query.shape[0]);token_width=14*width if chain.include_tokens else 0
        bank=_load_frozen_bank(_path(link['bank_path']),band,source['checkpoint_sha256'],width,args.device)
        gate=_load_frozen_gate(_path(link['gate_path']),band,source['checkpoint_sha256'],link['bank_sha256'],
                               width,token_width,chain.include_tokens,args.device)
        chain.append(band,bank,gate)
    if not state['pending']:
        state['status']='target_reached_sample_certified';atomic_json(mpath,state)
    print('ZERO_AUTO_SUMMARY='+json.dumps({'status':state['status'],
          'promoted_total':len(state['bands']),'new_promoted':state['completed_new'],
          'covered_to':state['bands'][-1]['band'][1],
          'remaining_planned':len(state['pending']),'manifest':str(mpath)},sort_keys=True),flush=True)
    return state


def predict(args):
    out=_path(args.output)
    state=_read(out/'manifest.json');conf=_read(out/'config.json')
    roots=conf['root_paths']
    for key,value in [('ladder_output',roots['ladder']),('prior_bank_output',roots['prior_bank']),
                      ('prior_router_output',roots['prior_router']),('repair_output',roots['repair'])]:
        setattr(args,key,value)
    base,source=load_certified_model(roots['ladder'],args.device)
    chain=_load_chain(args,state,base,source)
    if max(abs(args.a),abs(args.b))>state['bands'][-1]['band'][1]:
        raise RuntimeError('ZERO_AUTO_PREDICT_OUTSIDE_PROMOTED_COVERAGE')
    # Live inference never computes A+B, even as a discarded label.
    operands=torch.tensor([[args.a,args.b]],dtype=torch.long,device=args.device)
    captured={}
    def capture(_module,_inputs,features):captured['features']=features.detach()
    hook=base.norm.register_forward_hook(capture)
    with torch.inference_mode():
        try:pred=base(operands)
        finally:hook.remove()
        if 'features' not in captured:raise RuntimeError('ZERO_AUTO_MISSING_LIVE_FEATURES')
        originals=torch.cat((pred['sign'],pred['digits'].reshape(1,70)),dim=-1)
        tokens=(base.input(operands).flatten(1) if chain.include_tokens
                else captured['features'].new_empty((1,0)))
        cached={'features':captured['features'],'tokens':tokens,'original':originals}
        out_logits,_=chain.existing(cached,1)
    sign=out_logits[:,:3].argmax(-1).long()-1
    digits=out_logits[:,3:].reshape(-1,7,10).argmax(-1).long()
    powers=digits.new_tensor([10**i for i in range(7)])
    result=int((sign*(digits*powers).sum(-1))[0])
    record={'a':args.a,'b':args.b,'prediction':result,
            'routing':'learned_hard_neural_only','covered_to':state['bands'][-1]['band'][1]}
    print('ZERO_AUTO_PREDICT='+json.dumps(record,sort_keys=True),flush=True)
    return record


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('run','plan','predict'),default='run')
    p.add_argument('--ladder-output',default='')
    p.add_argument('--prior-bank-output',default='')
    p.add_argument('--prior-router-output',default='')
    p.add_argument('--repair-output',default='')
    p.add_argument('--output',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--target',type=int,default=TARGET_LIMIT)
    p.add_argument('--max-stages',type=int,default=30,
                   help='Number of new accepted banks to train this invocation')
    p.add_argument('--failure-policy',choices=('stop','split'),default='split')
    p.add_argument('--min-width',type=int,default=16)
    p.add_argument('--bank-steps',type=int,default=1500)
    p.add_argument('--gate-steps',type=int,default=1200)
    p.add_argument('--gate-patience',type=int,default=5)
    p.add_argument('--gate-min-dev-gain',type=float,default=.001)
    p.add_argument('--bank-probe-every',type=int,default=250)
    p.add_argument('--gate-probe-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--sample-per-cell',type=int,default=96)
    p.add_argument('--history-samples',type=int,default=24)
    p.add_argument('--full-legacy-retention',action=argparse.BooleanOptionalAction,default=True)
    p.add_argument('--bank-hidden',type=int,default=256)
    p.add_argument('--gate-hidden',type=int,default=128)
    p.add_argument('--bank-lr',type=float,default=.0003)
    p.add_argument('--gate-lr',type=float,default=.00015)
    p.add_argument('--min-accuracy',type=float,default=.90)
    p.add_argument('--bank-max-steps',type=int,default=9000,
                   help='Upper bound on steps in each new bank, including initial block')
    p.add_argument('--bank-extension-steps',type=int,default=1500)
    p.add_argument('--bank-patience',type=int,default=2,
                   help='Stop if best DEV gain stays below threshold for this many extensions')
    p.add_argument('--bank-min-dev-gain',type=float,default=.002,
                   help='Minimum best-DEV accuracy gain over an extension block')
    p.add_argument('--bank-target-accuracy',type=float,default=.90,
                   help='Stop extending a bank once it meets this DEV accuracy')
    p.add_argument('--bank-warmstart-stage',default='',
                   help='Optional read-only path to old same-band stage with bank_best.pt')
    # Historical --min-route is accepted only for CLI compatibility. It is ignored.
    p.add_argument('--min-route',type=float,default=.90,
                   help='Deprecated and ignored; routing judged on arithmetic accuracy')
    p.add_argument('--min-zero-accuracy',type=float,default=1.0)
    p.add_argument('--min-near-accuracy',type=float,default=.95)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--a',type=int,default=170)
    p.add_argument('--b',type=int,default=-154)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.phase=='plan':
        print('ZERO_AUTO_PLAN='+json.dumps({'bands':proposed_bands(START,args.target),
              'new_bank_count':len(proposed_bands(START,args.target)),'target':args.target},sort_keys=True))
        return proposed_bands(START,args.target)
    if not args.output:raise ValueError('ZERO_AUTO_OUTPUT_REQUIRED')
    if args.phase=='predict':return predict(args)
    if any(not x for x in (args.ladder_output,args.prior_bank_output,args.prior_router_output,args.repair_output)):
        raise ValueError('ZERO_AUTO_BOOTSTRAP_PATHS_REQUIRED')
    return run(args)

if __name__=='__main__':main()
