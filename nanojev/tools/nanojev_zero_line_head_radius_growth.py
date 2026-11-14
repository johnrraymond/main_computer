#!/usr/bin/env python3
"""Frozen-backbone zero-line readout-radius training and held-out measurement.

The arithmetic oracle is used exclusively to generate labeled examples and
score predictions. During inference the model sees only A and B. Certified
radius-127 weights never change; sign/digit heads may be optimized separately.
No Z, tool executor, routers, or arithmetic experts.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random

import torch
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import POWERS, target_digits
from tools.nanojev_zero_line_ladder import prediction_values
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.zero-line-head-radius-growth.v1'
RADII = (16, 32, 64, 128)
TRAIN_BANDS = ((128, 159), (160, 191), (192, 223), (224, 255))
OOD_BANDS = ((256, 319), (320, 383), (384, 447), (448, 511))


def fold(a: int, b: int, seed: int) -> str:
    # Keep operand order swaps AND global sign mirrors in the same partition.
    key = tuple(sorted((abs(a), abs(b))))
    h = hashlib.blake2b(f'{seed}:{key[0]}:{key[1]}'.encode(), digest_size=8).digest()
    return 'dev' if int.from_bytes(h, 'little') % 2 == 0 else 'train'


def sampled_cards(max_delta, bands, seed, split, per_cell):
    """Deterministic matched strata: delta x magnitude-band x sign of A.

    Rows are sampled without replacements. Train/dev use disjoint unsigned
    magnitude pair families; OOD uses entirely unseen operand magnitudes.
    """
    if max_delta < 1 or per_cell < 1:
        raise ValueError('ZERO_RADIUS_BAD_DATA_REQUEST')
    cards = []
    for delta in range(-max_delta, max_delta + 1):
        for band_index, (lo, hi) in enumerate(bands):
            for orientation in (-1, 1):
                candidates = []
                for mag_a in range(1, hi + 1):
                    a = orientation * mag_a
                    b = delta - a
                    magnitude = max(abs(a), abs(b))
                    if not lo <= magnitude <= hi:
                        continue
                    if split != 'ood' and fold(a, b, seed) != split:
                        continue
                    candidates.append((a, b))
                rng = random.Random(f'{seed}:{split}:{delta}:{lo}:{orientation}')
                rng.shuffle(candidates)
                for a, b in candidates[:per_cell]:
                    cards.append((a, b, delta, band_index, orientation))
    return cards


def select(cards, maximum):
    return [c for c in cards if -maximum <= c[2] <= maximum]


def samples_by_sign_delta_band(cards):
    counts = {}
    for a,b,d,band,orientation in cards:
        key=(d,band,orientation)
        counts[key] = counts.get(key,0)+1
    return counts


def certify(metrics, min_rate, bands=4):
    """Require EVERY integer residual, both signs of A, every magnitude band.

    A missing cell fails closed. This prevents claiming a radius from sparse
    sampling, zero-only success, or one favorable sign direction.
    """
    tested = metrics['cells']
    max_r = metrics['max_delta']
    for band in range(bands):
        for orient in (-1,1):
            zero = tested.get(f'0:{band}:{orient}')
            if not zero or zero['count'] < 1 or zero['rate'] < min_rate:
                return 0
    radius = 0
    for r in range(1, max_r + 1):
        frontier_ok = True
        for delta in (-r,r):
            for band in range(bands):
                for orient in (-1,1):
                    cell = tested.get(f'{delta}:{band}:{orient}')
                    if not cell or cell['count'] < 1 or cell['rate'] < min_rate:
                        frontier_ok=False
        if not frontier_ok:
            break
        radius = r
    return radius


@torch.inference_mode()
def evaluate(model, cards, device, batch_size, max_delta, min_rate=.8, include_rows=False):
    model.eval()
    counts = {}
    totals = {}
    mae = {}
    rows = []
    for start in range(0, len(cards), batch_size):
        chunk = cards[start:start+batch_size]
        x = torch.tensor([(a,b) for a,b,_,_,_ in chunk],dtype=torch.long,device=device)
        y = torch.tensor([d for _,_,d,_,_ in chunk],dtype=torch.long,device=device)
        output = model(x)
        values, signs, digits = prediction_values(output)
        pdigits=target_digits(y)
        passed = (values==y) & (signs==y.sign()) & (digits==pdigits).all(-1)
        for item, pred, correct in zip(chunk,values.cpu().tolist(),passed.cpu().tolist()):
            a,b,d,band,orient=item
            key=f'{d}:{band}:{orient}'
            counts[key]=counts.get(key,0)+int(correct)
            totals[key]=totals.get(key,0)+1
            mae[key]=mae.get(key,0)+abs(pred-d)
            if include_rows:
                rows.append((a,b,d,pred,band,orient,int(correct)))
    cells={k:{'count':v,'correct':counts[k], 'rate':counts[k]/v,'mae':mae[k]/v} for k,v in totals.items()}
    rate = sum(counts.values()) / max(1,sum(totals.values()))
    detailed={'max_delta':max_delta,'count':len(cards),'accuracy':rate,'cells':cells}
    detailed['certified_radius']=certify(detailed,min_rate)
    detailed['by_residual']={str(d): {'count':sum(v['count'] for k,v in cells.items() if int(k.split(':')[0])==d),
         'correct':sum(v['correct'] for k,v in cells.items() if int(k.split(':')[0])==d)}
         for d in range(-max_delta,max_delta+1)}
    for entry in detailed['by_residual'].values():
        entry['rate']=entry['correct']/entry['count'] if entry['count'] else None
    return detailed, rows


def new_batch(train_cards, radius, count, rng):
    """Mix near-zero retention, current frontier, and full learned radius."""
    retained=[c for c in train_cards if abs(c[2])<=2]
    frontier=[c for c in train_cards if max(0,radius//2) < abs(c[2])<=radius]
    current=select(train_cards,radius)
    def pick(pool,k):
        if not pool:
            raise RuntimeError('ZERO_RADIUS_EMPTY_TRAIN_POPULATION')
        return [pool[rng.randrange(len(pool))] for _ in range(k)]
    low=count//3
    high=count//3
    batch=pick(retained,low)+pick(frontier,high)+pick(current,count-low-high)
    rng.shuffle(batch)
    return batch


def checkpoint_data(model,optimizer,stage,step,rng,history,best,source):
    return {'schema':SCHEMA+'.checkpoint', 'model':model.state_dict(),
            'optimizer':optimizer.state_dict(),'stage':stage,'step':step,
            'random_state':rng.getstate(), 'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'history':history,'best':best,'source_sha256':source['checkpoint_sha256']}


def atomic_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
    os.replace(tmp,path)


def atomic_save(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp'); torch.save(obj,tmp); os.replace(tmp,path)


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_RADIUS_CUDA_UNAVAILABLE')
    if args.max_residual not in RADII or args.batch_size<3 or args.eval_batch_size<1 or args.steps_per_stage<1 or args.probe_every<1 or not 0 < args.lr < 1 or args.train_per_cell<1 or args.dev_per_cell<1 or args.ood_per_cell<1 or not 0 < args.threshold <= 1 or not 0 < args.retention_floor <= 1:
        raise ValueError('ZERO_RADIUS_BAD_OPTIONS')
    torch.set_num_threads(args.cpu_threads)
    out=Path(args.output).expanduser().resolve()
    base=Path(args.ladder_output).expanduser().resolve()
    if out==base or out in base.parents or base in out.parents:
        raise RuntimeError('ZERO_RADIUS_SOURCE_OUTPUT_OVERLAP')
    model,source=load_certified_model(base,args.device)
    source_frozen_values = {k:p.detach().cpu().clone() for k,p in model.named_parameters()
                            if not k.startswith(('sign_head.', 'digit_head.'))}
    for key,p in model.named_parameters():
        p.requires_grad_(key.startswith(('sign_head.', 'digit_head.')))
    if args.phase=='train':
        if args.head_checkpoint is not None:
            raise ValueError('ZERO_RADIUS_TRAIN_REQUIRES_CERTIFIED_SOURCE_OR_RESUME')
        if out.exists() and any(out.iterdir()) and not args.resume:
            raise FileExistsError('ZERO_RADIUS_OUTPUT_EXISTS')
    elif args.resume:
        raise ValueError('ZERO_RADIUS_PROBE_CANNOT_RESUME')
    if args.phase=='probe' and out.exists() and any(out.iterdir()):
        raise FileExistsError('ZERO_RADIUS_OUTPUT_EXISTS')
    train=sampled_cards(args.max_residual,TRAIN_BANDS,args.seed,'train',args.train_per_cell)
    dev=sampled_cards(args.max_residual,TRAIN_BANDS,args.seed,'dev',args.dev_per_cell)
    ood=sampled_cards(args.max_residual,OOD_BANDS,args.seed,'ood',args.ood_per_cell)
    # Retention is the full radius-127 deck, not a subset or just zero cards.
    from tools.nanojev_zero_line_ladder import deck
    known=deck(127)
    if args.resume or args.head_checkpoint:
        path=out/'latest.pt' if args.resume else Path(args.head_checkpoint)
        ckpt=torch.load(path,map_location='cpu',weights_only=True)
        if ckpt.get('schema')!=SCHEMA+'.checkpoint' or ckpt.get('source_sha256')!=source['checkpoint_sha256']:
            raise RuntimeError('ZERO_RADIUS_HEAD_SOURCE_MISMATCH')
        model.load_state_dict(ckpt['model'],strict=True)
    for name,param in model.named_parameters():
        if name in source_frozen_values and not torch.equal(param.detach().cpu(),source_frozen_values[name]):
            raise RuntimeError('ZERO_RADIUS_BACKBONE_FROM_HEAD_CHECKPOINT_DIFFERS')
    fingerprint={'schema':SCHEMA,'source':source,'max_residual':args.max_residual,
                 'steps_per_stage':args.steps_per_stage,'seed':args.seed,'train_per_cell':args.train_per_cell,
                 'dev_per_cell':args.dev_per_cell,'ood_per_cell':args.ood_per_cell,
                 'threshold':args.threshold,'retention_floor':args.retention_floor,
                 'learning_rate':args.lr,'batch_size':args.batch_size,'probe_every':args.probe_every}
    if args.resume:
        cfg=json.loads((out/'config.json').read_text())
        if cfg!=fingerprint: raise RuntimeError('ZERO_RADIUS_CONFIG_DRIFT')
    else:
        out.mkdir(parents=True,exist_ok=True)
        atomic_json(out/'config.json',fingerprint)
    start_dev,_=evaluate(model,dev,args.device,args.eval_batch_size,args.max_residual,args.threshold)
    start_ood,_=evaluate(model,ood,args.device,args.eval_batch_size,args.max_residual,args.threshold)
    if args.phase=='train' and not args.resume:
        atomic_json(out/'initial_probe.json',{'schema':SCHEMA,'dev':start_dev,'ood':start_ood,
                                             'source':source,'test_untouched':True})
    if args.phase=='probe':
        report={'schema':SCHEMA,'mode':'probe','baseline_dev':start_dev,'baseline_ood':start_ood,
                'no_training':True,'source':source,'test_untouched':True}
        atomic_json(out/'result.json',report)
        print('ZERO_RADIUS_PROBE='+json.dumps({'dev_radius':start_dev['certified_radius'],
              'ood_radius':start_ood['certified_radius'],'dev_accuracy':start_dev['accuracy'],
              'ood_accuracy':start_ood['accuracy'],'report':str(out/'result.json')}),flush=True)
        if sha256(source['checkpoint']) != source['checkpoint_sha256']:
            raise RuntimeError('ZERO_RADIUS_SOURCE_CHANGED')
        return report
    if args.phase=='train' and args.resume and not (out/'initial_probe.json').is_file():
        raise RuntimeError('ZERO_RADIUS_INITIAL_PROBE_MISSING')
    optimizer=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=args.lr,weight_decay=0)
    rng=random.Random(args.seed+808)
    history=[]; best=(-1,-1.,-1.)
    stages=[r for r in RADII if r<=args.max_residual]
    stage_index=0; stage_step=0
    if args.resume:
        optimizer.load_state_dict(ckpt['optimizer'])
        rng.setstate(ckpt['random_state']); torch.set_rng_state(ckpt['torch_rng'])
        if ckpt['cuda_rng'] is not None and torch.cuda.is_available():torch.cuda.set_rng_state_all(ckpt['cuda_rng'])
        stage_index=ckpt['stage']; stage_step=ckpt['step']; history=ckpt['history']; best=tuple(ckpt['best'])
    else:
        base_report={'schema':SCHEMA,'mode':'train','baseline_dev':start_dev,'baseline_ood':start_ood,
                    'source':source,'history':[],'test_untouched':True}
        atomic_json(out/'result.json',base_report)
    for j in range(stage_index,len(stages)):
        radius=stages[j]
        if j!=stage_index:stage_step=0
        for step in range(stage_step+1,args.steps_per_stage+1):
            model.train()
            samples=new_batch(train,radius,args.batch_size,rng)
            x=torch.tensor([(a,b) for a,b,_,_,_ in samples],dtype=torch.long,device=args.device)
            y=torch.tensor([d for _,_,d,_,_ in samples],dtype=torch.long,device=args.device)
            logits=model(x)
            loss=F.cross_entropy(logits['sign'],y.sign().long()+1)+2*F.cross_entropy(logits['digits'].transpose(1,2),target_digits(y))
            optimizer.zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],1.,error_if_nonfinite=True)
            optimizer.step()
            if step%args.probe_every!=0 and step!=args.steps_per_stage:continue
            dev_score,_=evaluate(model,dev,args.device,args.eval_batch_size,args.max_residual,args.threshold)
            ood_score,_=evaluate(model,ood,args.device,args.eval_batch_size,args.max_residual,args.threshold)
            retention=0; accurate=0; total=0
            with torch.inference_mode():
                for chunk in known['x'].split(args.eval_batch_size):
                    z=chunk.to(args.device); output=model(z);pred,sign,digits=prediction_values(output)
                    actual=z.sum(-1); good=(pred==actual)&(sign==actual.sign())&(digits==target_digits(actual)).all(-1)
                    accurate+=int(good.sum());total+=len(chunk)
            retention=accurate/total
            record={'stage_radius':radius,'step':step,'dev_radius':dev_score['certified_radius'],
                    'ood_radius':ood_score['certified_radius'],'dev_accuracy':dev_score['accuracy'],
                    'ood_accuracy':ood_score['accuracy'],'retention':retention,
                    'loss':float(loss.detach()),'dev':dev_score,'ood':ood_score}
            history.append(record)
            score=(int(retention>=args.retention_floor),dev_score['certified_radius'],dev_score['accuracy'])
            if retention >= args.retention_floor and score>best:
                best=score
                atomic_save(out/'best.pt',checkpoint_data(model,optimizer,j,step,rng,history,best,source))
            atomic_json(out/'result.json',{'schema':SCHEMA,'source':source,
                'mode':'train','history':history,'latest':record,'best_rank':best,
                'baseline_file':'initial_probe.json',
                'test_untouched':True,'backbone_frozen':True})
            print('ZERO_RADIUS_HEAD stage=±'+str(radius)+' step='+str(step)+'/'+str(args.steps_per_stage)+
                  ' loss='+f'{float(loss.detach()):.5f}'+' DEV_r='+str(dev_score['certified_radius'])+
                  ' OOD_r='+str(ood_score['certified_radius'])+' DEV='+f'{dev_score["accuracy"]:.4f}'+
                  ' OOD='+f'{ood_score["accuracy"]:.4f}'+ ' retain='+f'{retention:.5f}',flush=True)
            atomic_save(out/'latest.pt',checkpoint_data(model,optimizer,j,step,rng,history,best,source))
        atomic_save(out/'latest.pt',checkpoint_data(model,optimizer,j+1,0,rng,history,best,source))
        stage_step=0
    if sha256(source['checkpoint']) != source['checkpoint_sha256']:
        raise RuntimeError('ZERO_RADIUS_SOURCE_CHANGED')
    for name, param in model.named_parameters():
        if name in source_frozen_values and not torch.equal(param.detach().cpu(), source_frozen_values[name]):
            raise RuntimeError('ZERO_RADIUS_BACKBONE_MUTATED')
    with (out/'radius_growth.csv').open('w',newline='',encoding='utf-8') as fp:
        writer=csv.writer(fp)
        writer.writerow(['stage_radius','step','dev_certified_radius','ood_certified_radius',
                         'dev_exact_rate','ood_exact_rate','original_radius127_retention'])
        for rec in history:
            writer.writerow([rec['stage_radius'],rec['step'],rec['dev_radius'],rec['ood_radius'],
                             rec['dev_accuracy'],rec['ood_accuracy'],rec['retention']])
    print('ZERO_RADIUS_SOURCE_IMMUTABLE=TRUE BACKBONE_FROZEN=TRUE TEST_UNTOUCHED=TRUE',flush=True)
    return history


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('probe','train'),default='probe')
    p.add_argument('--ladder-output',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--head-checkpoint',type=Path)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--max-residual',type=int,default=128,choices=RADII)
    p.add_argument('--steps-per-stage',type=int,default=750)
    p.add_argument('--probe-every',type=int,default=150)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--train-per-cell',type=int,default=12)
    p.add_argument('--dev-per-cell',type=int,default=6)
    p.add_argument('--ood-per-cell',type=int,default=6)
    p.add_argument('--retention-floor',type=float,default=1.0)
    p.add_argument('--threshold',type=float,default=.8)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):return run(parser().parse_args(argv))
if __name__=='__main__':main()
