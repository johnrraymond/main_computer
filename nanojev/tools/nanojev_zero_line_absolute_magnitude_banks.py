#!/usr/bin/env python3
"""Independent input-ABSOLUTE-magnitude banks supporting the certified zero line.

Bank ownership is determined by max(abs(A),abs(B)), available BEFORE inference.
The target A+B never chooses a bank, a correction, or a fallback. A bank
predicts the COMPLETE signed answer from frozen recurrent features. All prior
networks are immutable. M<=127 always uses the certified source unmodified.

A 16:32 bank is a diagnostic only: it cannot be deployed over the certified
radius-127 domain. The first possible extension is 128:159.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.zero-line-absolute-magnitude-banks.v1'
SOURCE_RADIUS = 127
GROUPS = ('zero', 'near', 'inner', 'frontier', 'mid', 'far')



@torch.no_grad()
def encode_base(model, operands, device, batch_size):
    """Record frozen recurrent features and original logits from the source."""
    model.eval()
    features, outputs = [], []
    slot = {}
    def capture(_module, _inputs, output):
        slot['value'] = output.detach()
    handle = model.norm.register_forward_hook(capture)
    try:
        for chunk in operands.split(batch_size):
            slot.clear()
            result = model(chunk.to(device))
            if 'value' not in slot:
                raise RuntimeError('ZERO_ABS_FROZEN_STATE_UNAVAILABLE')
            features.append(slot['value'].detach())
            outputs.append(torch.cat((result['sign'],result['digits'].reshape(-1,70)),-1).detach())
    finally:
        handle.remove()
    return torch.cat(features),torch.cat(outputs)


def make_cache(model, cards, device, batch_size):
    """No gold residual, baseline prediction, or confidence enters features."""
    if isinstance(cards,dict):
        operands=cards['x'].long()
        target=cards['y'].long()
    else:
        if not cards:
            raise ValueError('ZERO_ABS_EMPTY_CACHE')
        operands=torch.tensor([(a,b) for a,b,_,_,_ in cards],dtype=torch.long)
        target=torch.tensor([d for _,_,d,_,_ in cards],dtype=torch.long)
    features,original=encode_base(model,operands,device,batch_size)
    return {'features':features,'original':original,'target':target.to(device),
            'operands':operands}


def exact_mask(logits,truth):
    """All sign and every digit must match, including zero's sign."""
    sign=logits[:,:3].argmax(-1).long()-1
    digits=logits[:,3:].reshape(-1,7,10).argmax(-1).long()
    powers=digits.new_tensor([10**i for i in range(7)])
    value=sign*(digits*powers).sum(-1)
    return (value==truth)&(sign==truth.sign())&(digits==target_digits(truth)).all(-1)

def parse_bands(value):
    bands = []
    for token in value.split(','):
        try:
            lo, hi = map(int, token.strip().split(':'))
        except (ValueError, TypeError) as e:
            raise ValueError('ZERO_ABS_BAD_BANDS') from e
        if not 1 <= lo <= hi <= 511 or any(not (hi < x or lo > y) for x,y in bands):
            raise ValueError('ZERO_ABS_OVERLAPPING_OR_INVALID_BANDS')
        bands.append((lo,hi))
    if not bands or sorted(bands) != bands:
        raise ValueError('ZERO_ABS_UNSORTED_BANDS')
    if any(lo <= SOURCE_RADIUS < hi for lo,hi in bands):
        raise ValueError('ZERO_ABS_BANK_STRADDLES_CERTIFIED_BOUNDARY')
    return tuple(bands)


def absolute_magnitude(a, b):
    return max(abs(int(a)),abs(int(b)))


def bank_for_inputs(a, b, bands, *, source_radius=SOURCE_RADIUS):
    """Input-only routing. Never inspect a prediction or A+B."""
    m = absolute_magnitude(a,b)
    if m <= source_radius:
        return None
    for i,(lo,hi) in enumerate(bands):
        if lo <= m <= hi:
            return i
    return None


def residual_group(delta):
    n=abs(delta)
    if n == 0: return 'zero'
    if n <= 2: return 'near'
    if n <= 15: return 'inner'
    if n <= 32: return 'frontier'
    if n <= 64: return 'mid'
    return 'far'


def family_fold(a,b,seed):
    x,y=sorted((abs(a),abs(b)))
    h=hashlib.blake2b(f'{seed}:{x}:{y}'.encode(),digest_size=8).digest()
    return 'train' if int.from_bytes(h,'little')%4 != 0 else 'dev'


def heldout_magnitude(m,seed):
    return (m + seed) % 5 == 0


def sample_absolute_band(low, high, seed, per_stratum=256, *, extrapolation=False):
    """Enumerate the actual Cartesian magnitude shell, then stratify.

    OOD is disjoint in maximum ABSOLUTE operand magnitude (not residual).
    TRAIN/DEV are disjoint magnitude-pair families with swap/sign mirrors kept
    together. Oracle sums appear only in TRAIN loss and scoring.
    """
    if not 1 <= low <= high <= 511 or per_stratum < 1:
        raise ValueError('ZERO_ABS_INVALID_SAMPLE_REQUEST')
    buckets={split:{} for split in ('train','dev','ood')}
    for a in range(-high,high+1):
        for b in range(-high,high+1):
            mag=absolute_magnitude(a,b)
            if not low <= mag <= high:
                continue
            if extrapolation:
                split='ood'
            else:
                split='ood' if heldout_magnitude(mag,seed) else family_fold(a,b,seed)
            delta=a+b
            key=(residual_group(delta), (1 if delta>0 else -1 if delta<0 else 0),
                 (1 if a>=0 else -1))
            buckets[split].setdefault(key,[]).append((a,b,delta,0,key[-1]))
    outputs={}
    for split,pools in buckets.items():
        selected=[]
        for key in sorted(pools):
            pool=pools[key]
            random.Random(f'{seed}:{low}:{high}:{split}:{key}').shuffle(pool)
            selected.extend(pool[:per_stratum])
        random.Random(f'{seed}:{split}:{low}:{high}:merge').shuffle(selected)
        outputs[split]=selected
    if not extrapolation and any(not outputs[name] for name in outputs):
        raise RuntimeError('ZERO_ABS_EMPTY_SPLIT')
    return outputs


class AbsoluteBank(nn.Module):
    """Ordinary MLP, no Gaussian, no residual coordinate, no first answer input."""
    def __init__(self,width,hidden=256):
        super().__init__()
        self.net=nn.Sequential(nn.LayerNorm(width),nn.Linear(width,hidden),nn.GELU(),
                               nn.Linear(hidden,hidden),nn.GELU(),nn.Linear(hidden,73))

    def forward(self,features):
        return self.net(features)


@torch.inference_mode()
def bank_logits(bank, cache, batch_size=1024):
    bank.eval()
    return torch.cat([bank(cache['features'][i:i+batch_size])
                      for i in range(0,len(cache['target']),batch_size)],0)


def mask_group(y,group):
    v=y.abs()
    if group=='zero': return v==0
    if group=='near': return (v>=1)&(v<=2)
    if group=='inner': return (v>=3)&(v<=15)
    if group=='frontier': return (v>=16)&(v<=32)
    if group=='mid': return (v>=33)&(v<=64)
    if group=='far': return v>=65
    raise ValueError('ZERO_ABS_GROUP_INVALID')


def score(logits,cache,*,baseline=None):
    if len(cache['target'])==0: return {'cards':0,'accuracy':None}
    y=cache['target']
    correct=exact_mask(logits,y)
    result={'cards':len(y),'correct':int(correct.sum()),'accuracy':float(correct.float().mean()),
            'groups':{}, 'positive_accuracy':None, 'negative_accuracy':None}
    if baseline is not None:
        prior=exact_mask(baseline,y)
        result['newly_wrong']=int((prior&~correct).sum())
        result['newly_correct']=int((~prior&correct).sum())
    for group in GROUPS:
        mask=mask_group(y,group)
        if bool(mask.any()):
            entry={'cards':int(mask.sum()),'correct':int(correct[mask].sum()),
                   'accuracy':float(correct[mask].float().mean())}
            if baseline is not None:
                prior=exact_mask(baseline,y)
                entry['newly_wrong']=int((prior[mask]&~correct[mask]).sum())
                entry['baseline_accuracy']=float(prior[mask].float().mean())
            result['groups'][group]=entry
    for label,msk in (('positive',y>0),('negative',y<0)):
        if bool(msk.any()):result[label+'_accuracy']=float(correct[msk].float().mean())
    return result


@torch.inference_mode()
def assembled_logits(cache, accepted, candidate=None,*, source_radius=SOURCE_RADIUS):
    """Use certified base for M<=source radius, bank otherwise by INPUT M.

    accepted/candidate: sequence of ((lo,hi), bank). No oracle, residual,
    baseline answer, confidence, or classification of first answer routes.
    """
    logits=cache['original'].clone()
    mag=cache['operands'].abs().amax(dim=-1).to(logits.device)
    for (low,high),bank in [*accepted, *([candidate] if candidate else [])]:
        mask=(mag>=low)&(mag<=high)&(mag>source_radius)
        if mask.any():
            bank.eval()
            logits[mask]=bank(cache['features'][mask])
    return logits


def loss_for(logits,values):
    return (F.cross_entropy(logits[:,:3],values.sign().long()+1)
            +2*F.cross_entropy(logits[:,3:].reshape(-1,7,10).transpose(1,2),target_digits(values)))


def train_bank(bank, train, dev, ood, *, steps, batch_size, probe_every,lr,seed, prefix):
    optimizer=torch.optim.AdamW(bank.parameters(),lr=lr,weight_decay=0.)
    rng=random.Random(seed)
    pools={g:torch.where(mask_group(train['target'],g))[0].tolist() for g in GROUPS}
    if not pools['zero'] or not pools['near'] or not pools['frontier']:
        raise RuntimeError('ZERO_ABS_INSUFFICIENT_CURRICULUM_GROUPS')
    # Explicit zero-line weighting is a TRAIN curriculum, not a route.
    weights={'zero':2,'near':3,'inner':2,'frontier':4,'mid':2,'far':3}
    choices=[g for g in GROUPS for _ in range(weights[g]) if pools[g]]
    history=[]
    for step in range(1,steps+1):
        ids=[pools[g][rng.randrange(len(pools[g]))]
             for g in (choices[rng.randrange(len(choices))] for _ in range(batch_size))]
        idx=torch.tensor(ids,device=train['target'].device)
        bank.train()
        output=bank(train['features'][idx])
        loss=loss_for(output,train['target'][idx])
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(bank.parameters(),1.)
        optimizer.step()
        if step%probe_every and step!=steps:continue
        tr=score(bank_logits(bank,train),train)
        dv=score(bank_logits(bank,dev),dev)
        od=score(bank_logits(bank,ood),ood)
        row={'step':step,'train_loss':float(loss.detach()),'train':tr,'dev':dv,'ood':od}
        history.append(row)
        print(f'ZERO_ABS_BANK {prefix} step={step}/{steps} loss={row["train_loss"]:.5f} '
              f'TRAIN={tr["accuracy"]:.4f} DEV={dv["accuracy"]:.4f} OOD={od["accuracy"]:.4f} '
              f'DEV_zero={dv["groups"].get("zero",{}).get("accuracy",0):.4f} '
              f'OOD_frontier={od["groups"].get("frontier",{}).get("accuracy",0):.4f}',flush=True)
    return history


def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    os.replace(tmp,path)


def atomic_pt(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.pt.tmp');torch.save(data,tmp);os.replace(tmp,path)


def run(args):
    bands=parse_bands(args.bands)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_ABS_CUDA_UNAVAILABLE')
    if any(v<1 for v in (args.steps_per_bank,args.probe_every,args.batch_size,
                          args.eval_batch_size,args.per_stratum,args.cpu_threads,args.hidden)) \
            or not 0<args.lr<1 or not 0<args.min_accuracy<=1:
        raise ValueError('ZERO_ABS_BAD_OPTIONS')
    source_root=Path(args.ladder_output).expanduser().resolve()
    output=Path(args.output).expanduser().resolve()
    if output==source_root or output in source_root.parents or source_root in output.parents:
        raise RuntimeError('ZERO_ABS_SOURCE_OUTPUT_OVERLAP')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('ZERO_ABS_OUTPUT_EXISTS')
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(args.seed)
    base,source=load_certified_model(source_root,args.device)
    base.eval()
    for p in base.parameters():p.requires_grad_(False)
    old=make_cache(base,deck(SOURCE_RADIUS),args.device,args.eval_batch_size)
    if not bool(exact_mask(old['original'],old['target']).all()):
        raise RuntimeError('ZERO_ABS_BASE_CERTIFIED_DECK_FAILED')
    config={'schema':SCHEMA,'bands':bands,'source_sha256':source['checkpoint_sha256'],
            'source_step':source['step'],'routing':'max_abs_operand_input_only',
            'signed_sum_used_for':'train_labels_and_metrics_only',
            'parameters':{k:v for k,v in vars(args).items() if k not in ('ladder_output','output','device')}}
    output.mkdir(parents=True,exist_ok=True)
    atomic_json(output/'config.json',config)
    accepted=[]
    reports=[]
    for i,(low,high) in enumerate(bands):
        tag=f'{low}_{high}'
        populations=sample_absolute_band(low,high,args.seed,args.per_stratum)
        train=make_cache(base,populations['train'],args.device,args.eval_batch_size)
        dev=make_cache(base,populations['dev'],args.device,args.eval_batch_size)
        ood=make_cache(base,populations['ood'],args.device,args.eval_batch_size)
        larger_band=None
        # Adjacent unseen higher-magnitude extrapolation: diagnostic only.
        if high<511:
            upper=min(511,high+(high-low+1))
            farther=sample_absolute_band(high+1,upper,args.seed,args.per_stratum,extrapolation=True)
            if farther['ood']:
                larger_band=make_cache(base,farther['ood'],args.device,args.eval_batch_size)
        baseline_dev=score(dev['original'],dev)
        baseline_ood=score(ood['original'],ood)
        torch.manual_seed(args.seed+100*i)
        bank=AbsoluteBank(int(base.query.shape[0]),args.hidden).to(args.device)
        history=train_bank(bank,train,dev,ood,steps=args.steps_per_bank,
                           batch_size=args.batch_size,probe_every=args.probe_every,
                           lr=args.lr,seed=args.seed+77*i,prefix=f'M={low}:{high}')
        isolated_dev=score(bank_logits(bank,dev),dev,baseline=dev['original'])
        isolated_ood=score(bank_logits(bank,ood),ood,baseline=ood['original'])
        candidate=((low,high),bank)
        integrated_dev=score(assembled_logits(dev,accepted,candidate),dev,baseline=dev['original'])
        integrated_ood=score(assembled_logits(ood,accepted,candidate),ood,baseline=ood['original'])
        retention=score(assembled_logits(old,accepted,candidate),old)
        extrapolation=(score(bank_logits(bank,larger_band),larger_band)
                       if larger_band is not None else None)
        # For genuine out-of-source bands, preserve all previously correct
        # zero-line answers on both DEV and untouched-magnitude OOD.
        near_preserved=all(
            evaluation['groups'][name]['newly_wrong']==0
            for evaluation in (integrated_dev,integrated_ood)
            for name in ('zero','near') if name in evaluation['groups'])
        deployable=low>SOURCE_RADIUS
        # Promotion tests accuracy across the ABSOLUTE band; OOD magnitudes
        # were excluded from TRAIN/DEV. No OOD weights or gradients.
        eligible=(deployable and retention['accuracy']==1.
                  and near_preserved
                  and isolated_dev['accuracy']>=args.min_accuracy
                  and isolated_ood['accuracy']>=args.min_accuracy
                  and isolated_dev['groups'].get('frontier',{}).get('accuracy',0)>=args.min_accuracy
                  and isolated_ood['groups'].get('frontier',{}).get('accuracy',0)>=args.min_accuracy
                  and isolated_dev['groups'].get('zero',{}).get('accuracy',0)>=args.min_accuracy
                  and isolated_ood['groups'].get('zero',{}).get('accuracy',0)>=args.min_accuracy)
        record={'band':[low,high],'bank_kind':'plain_mlp','status':
                'DIAGNOSTIC_IN_CERTIFIED_DOMAIN' if not deployable else 'ELIGIBLE' if eligible else 'NOT_ELIGIBLE',
                'train_cards':len(train['target']),'dev_cards':len(dev['target']),
                'ood_cards':len(ood['target']),'history':history,
                'baseline':{'dev':baseline_dev,'ood':baseline_ood},
                'isolated':{'dev':isolated_dev,'ood':isolated_ood,'next_magnitude':extrapolation},
                'integrated':{'dev':integrated_dev,'ood':integrated_ood,'retention':retention},
                'near_zero_no_regressions':near_preserved,'eligible':eligible,
                'router':'input_magnitude_only','previous_banks_kept':len(accepted)}
        part=output/f'bank_{tag}'
        part.mkdir(parents=True,exist_ok=False)
        atomic_pt(part/'candidate.pt',{'schema':SCHEMA+'.candidate',
                 'source_sha256':source['checkpoint_sha256'],'band':[low,high],
                 'bank':bank.state_dict(),'optimizer_restart_required':True})
        atomic_json(part/'result.json',record)
        if eligible:
            accepted.append(candidate)
            atomic_pt(part/'promoted.pt',{'schema':SCHEMA+'.promoted',
                 'source_sha256':source['checkpoint_sha256'],'band':[low,high],
                 'bank':bank.state_dict()})
        reports.append(record)
        print('ZERO_ABS_RESULT='+json.dumps({'band':[low,high],
              'isolated_dev':isolated_dev['accuracy'],'isolated_ood':isolated_ood['accuracy'],
              'next_magnitude':extrapolation['accuracy'] if extrapolation else None,
              'center_new_errors_dev':sum(integrated_dev['groups'].get(g,{}).get('newly_wrong',0) for g in ('zero','near')),
              'center_new_errors_ood':sum(integrated_ood['groups'].get(g,{}).get('newly_wrong',0) for g in ('zero','near')),
              'original_retention':retention['accuracy'],'eligible':eligible,
              'deployable':deployable},sort_keys=True),flush=True)
    if sha256(source['checkpoint'])!=source['checkpoint_sha256']:
        raise RuntimeError('ZERO_ABS_SOURCE_MODIFIED')
    report={'schema':SCHEMA,'source_sha256':source['checkpoint_sha256'],
            'bands':bands,'results':reports,'promoted_bands':[list(k) for k,_ in accepted],
            'source_immutable':True,'original_deck_unchanged':True,'test_untouched':True}
    atomic_json(output/'result.json',report)
    print('ZERO_ABS_SUMMARY='+json.dumps({'promoted_bands':report['promoted_bands'],
           'results':[{'band':r['band'],'eligible':r['eligible'],
                        'dev':r['isolated']['dev']['accuracy'],
                        'ood':r['isolated']['ood']['accuracy']} for r in reports]},sort_keys=True),flush=True)
    print('ZERO_ABS_SOURCE_IMMUTABLE=TRUE ZERO_LINE_BASE_FROZEN=TRUE ABSOLUTE_INPUT_ROUTING=TRUE TEST_UNTOUCHED=TRUE',flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--bands',default='128:159')
    p.add_argument('--steps-per-bank',type=int,default=1500)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--per-stratum',type=int,default=256)
    p.add_argument('--hidden',type=int,default=256)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--min-accuracy',type=float,default=.8)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':main()
