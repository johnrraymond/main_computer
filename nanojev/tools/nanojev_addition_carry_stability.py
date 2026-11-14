#!/usr/bin/env python3
"""Carry/retention ablation from the immutable passed signed-stage NanoJev model.

Two independent arms start with identical source weights and see the same
three-digit arithmetic datasets. The only intervention is the amount of
balanced TRAIN-only rehearsal of older skills. No arithmetic rules appear
in neural inference, no TEST rows are generated, and no prior file is edited.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from torch import nn

from tools import nanojev_addition_curriculum as base
from tools import nanojev_addition_transfer_probe as transfer

SCHEMA = 'nanojev.addition-carry-stability.v1'
ARMS = ('balanced', 'retention_heavy')
CATEGORIES = ('no_carry', 'single_carry', 'chain_carry')
SOURCE_STAGES = ('atomic', 'no_carry', 'carry', 'signed')
REPLAY_WEIGHTS = {'atomic': 1, 'no_carry': 2, 'carry': 3, 'signed': 6}
REPLAY_FRACTIONS = {'balanced': 0.50, 'retention_heavy': 0.70}
OUTPUT_DIGITS = transfer.OUTPUT_DIGITS


def carry_count(a: int, b: int) -> int:
    """Only for dataset categorization; never used by the model."""
    assert a >= 0 and b >= 0
    carry, count = 0, 0
    while a or b or carry:
        total = a % 10 + b % 10 + carry
        carry = total // 10
        count += int(bool(carry))
        a //= 10
        b //= 10
    return count


def category(a: int, b: int) -> str:
    k = carry_count(a, b)
    return 'no_carry' if k == 0 else 'single_carry' if k == 1 else 'chain_carry'


def make_dataset(split: str, seed: int, per_group: int):
    if split not in ('train', 'dev', 'test') or per_group < 1:
        raise ValueError('CARRY_BAD_DATASET_ARGS')
    rows = []
    seen = set()
    for group in CATEGORIES:
        rng = random.Random(f'{seed}:carry3:{split}:{group}')
        found = 0
        for _ in range(per_group * 10000):
            a, b = rng.randrange(100, 1000), rng.randrange(100, 1000)
            key = tuple(sorted((a, b)))
            if category(a, b) != group or key in seen or base.pair_fold(a, b, seed) != split:
                continue
            seen.add(key)
            rows.append((a, b, a+b, group))
            found += 1
            if found == per_group:
                break
        if found != per_group:
            raise RuntimeError(f'CARRY_DATA_EXHAUSTED:{split}:{group}:{found}')
    random.Random(f'{seed}:carry3:{split}:shuffle').shuffle(rows)
    return {'x': torch.tensor([(a,b) for a,b,_,_ in rows], dtype=torch.long),
            'y': torch.tensor([c for _,_,c,_ in rows], dtype=torch.long),
            'groups': [g for *_,g in rows], 'branch': 'three_digit_carry',
            'split': split, 'kind': 'held_out_unordered_operand_pairs'}


def old_datasets(ck, replay_per_group, dev_per_group):
    opts = ck['identity']['options']
    train = {s: base.make_stage(s, 'train', opts['seed'], replay_per_group)
             for s in SOURCE_STAGES}
    dev = {s: base.make_stage(s, 'dev', opts['seed'], dev_per_group)
           for s in SOURCE_STAGES}
    return train, dev


def replay_allocation(batch_size, fraction):
    """Allocate every update across ALL prior stages, with extra signed replay."""
    total = round(batch_size * fraction)
    weights = sum(REPLAY_WEIGHTS.values())
    result = {stage: (total * w) // weights for stage, w in REPLAY_WEIGHTS.items()}
    remaining = total - sum(result.values())
    for s in ('signed', 'carry', 'no_carry', 'atomic'):
        if remaining <= 0:
            break
        result[s] += 1
        remaining -= 1
    assert sum(result.values()) == total
    return result


def sample_batch(current, replay, batch_size, counts, rng):
    old_total = sum(counts.values())
    new_total = batch_size - old_total
    samples = []
    for data, count in [(current, new_total), *[(replay[s], counts[s]) for s in SOURCE_STAGES]]:
        if count == 0:
            continue
        idx = torch.randint(len(data['y']), (count,), generator=rng)
        samples.append((data['x'].index_select(0, idx), data['y'].index_select(0, idx)))
    x = torch.cat([p[0] for p in samples])
    y = torch.cat([p[1] for p in samples])
    permutation = torch.randperm(len(y), generator=rng)
    return x[permutation], y[permutation]


def evaluate(model, data, device, batch, tolerance):
    return transfer.evaluate(model, data, device, batch, tolerance)


def retention(model, old_dev, device, batch, tolerance):
    return transfer.evaluate_retention(model, old_dev, device, batch, tolerance)


def decision(dev, old, threshold):
    current = min(dev['digit'][cat]['rate'] for cat in CATEGORIES)
    old_rates = {s: min(m['digit'][k]['rate'] for k in m['digit'] if k != 'all')
                 for s, m in old.items()}
    limits = {s: (1. if s == 'atomic' else threshold) for s in SOURCE_STAGES}
    feasible = current >= threshold and all(old_rates[s] >= limits[s] for s in SOURCE_STAGES)
    # Optimize the weakest *requirement*, so a DEV gain cannot conceal catastrophic forgetting.
    ratios = [current / threshold, *[old_rates[s] / limits[s] for s in SOURCE_STAGES]]
    return {'pass': feasible, 'dev_min': current, 'old_rates': old_rates,
            'retention_min': min(old_rates.values()), 'bottleneck_ratio': min(ratios),
            'gate': 'PASS' if feasible else 'FAIL'}


def rank(metrics, dev):
    return (int(metrics['pass']), metrics['bottleneck_ratio'],
            metrics['dev_min'], dev['digit']['all']['rate'])


def identity(args, source_hash, ck):
    opts = ('seed','learning_rate','steps','probe_every','batch_size','eval_batch_size',
            'train_per_group','dev_per_group','replay_per_group','tolerance',
            'threshold','cpu_threads','arms')
    return {'schema': SCHEMA, 'runner_sha256': base.sha256(__file__),
            'source_checkpoint': str(Path(args.signed_checkpoint).resolve()),
            'source_sha256': source_hash,
            'source_identity': ck['identity'],
            'options': {p: getattr(args,p) for p in opts if p != 'steps'},
            'policy': 'paired replay-fraction ablation; identical data and starting weights; train-only rehearsal; TEST untouched'}


def state(model, optimizer, rng, arm, step, best_rank, best_step, ident):
    return {'schema': SCHEMA+'.state','identity':ident,'arm':arm,'step':step,
            'best_rank': list(best_rank), 'best_step':best_step,
            'model':model.state_dict(),'optimizer':optimizer.state_dict(),
            'sample_rng':rng.get_state(),'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}


def run_arm(arm, args, ck, ident, current, old_train, old_dev, root):
    folder = root / 'arms' / arm
    latest = folder / 'latest.pt'
    if not args.resume and folder.exists() and any(folder.iterdir()):
        raise FileExistsError(f'CARRY_ARM_OVERWRITE_REFUSED:{arm}')
    folder.mkdir(parents=True, exist_ok=True)
    opts = ck['identity']['options']
    model = base.AdditionNet('digit_tokens', opts['width'], opts['heads'],
                             opts['layers'], opts['rounds'], opts['seed']).to(args.device)
    model.load_state_dict(ck['model'], strict=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.)
    torch.manual_seed(args.seed + 12345)
    rng = torch.Generator(device='cpu').manual_seed(args.seed + 45678)
    start = 0
    best_rank = (-1., -1., -1., -1.)
    best_step = 0
    if args.resume:
        if not latest.is_file():
            raise FileNotFoundError(f'CARRY_NO_RESUME_STATE:{arm}')
        st = torch.load(latest, map_location='cpu', weights_only=True)
        if st['identity'] != ident or st['arm'] != arm or st['schema'] != SCHEMA+'.state':
            raise RuntimeError('CARRY_RESUME_IDENTITY_MISMATCH')
        model.load_state_dict(st['model'], strict=True)
        optimizer.load_state_dict(st['optimizer'])
        rng.set_state(st['sample_rng'])
        torch.set_rng_state(st['torch_rng'])
        if st['cuda_rng'] is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(st['cuda_rng'])
        start, best_rank, best_step = st['step'], tuple(st['best_rank']), st['best_step']
        if args.steps < start:
            raise ValueError('CARRY_STEPS_BELOW_RESUME_STATE')
    else:
        baseline_dev = evaluate(model, current['dev'], args.device, args.eval_batch_size, args.tolerance)
        baseline_old = retention(model, old_dev, args.device, args.eval_batch_size, args.tolerance)
        transfer.write_json(folder / 'baseline.json',
                            {'dev':baseline_dev,'retention':baseline_old,
                             'gate':decision(baseline_dev,baseline_old,args.threshold)})
    counts = replay_allocation(args.batch_size, REPLAY_FRACTIONS[arm])
    for step in range(start+1, args.steps+1):
        model.train()
        x,y = sample_batch(current['train'], old_train, args.batch_size, counts, rng)
        x,y = x.to(args.device),y.to(args.device)
        out=model(x)
        loss=base.objective(out,x,y,OUTPUT_DIGITS)
        if not torch.isfinite(loss):
            raise FloatingPointError(f'CARRY_NONFINITE_LOSS:{arm}:{step}')
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
        optimizer.step()
        if step % args.probe_every and step != args.steps:
            continue
        dev = evaluate(model,current['dev'],args.device,args.eval_batch_size,args.tolerance)
        old = retention(model,old_dev,args.device,args.eval_batch_size,args.tolerance)
        train = evaluate(model,current['train'],args.device,args.eval_batch_size,args.tolerance)
        d=decision(dev,old,args.threshold)
        record={'arm':arm,'step':step,'train':train,'dev':dev,'retention':old,
                'gate':d,'replay_counts':counts}
        r=rank(d,dev)
        if r > best_rank:
            best_rank,best_step=r,step
            transfer.save_state(folder/'best.pt', {'schema':SCHEMA+'.best',
                            'identity':ident,'arm':arm,'step':step,
                            'model':model.state_dict(),'report':record})
            transfer.write_json(folder/'best.json',record)
        if d['pass'] and not (folder/'best_pass.pt').is_file():
            transfer.save_state(folder/'best_pass.pt', {'schema':SCHEMA+'.best_pass',
                            'identity':ident,'arm':arm,'step':step,
                            'model':model.state_dict(),'report':record})
        print(f'ADDITION_CARRY arm={arm} step={step}/{args.steps} '
              f'loss={loss.item():.5f} TRAIN={train["digit"]["all"]["rate"]:.4f} '
              f'DEV_min={d["dev_min"]:.4f} '
              f'DEV_no={dev["digit"]["no_carry"]["rate"]:.4f} '
              f'DEV_single={dev["digit"]["single_carry"]["rate"]:.4f} '
              f'DEV_chain={dev["digit"]["chain_carry"]["rate"]:.4f} '
              f'RETAIN_min={d["retention_min"]:.4f} '
              f'RETAIN_signed={d["old_rates"]["signed"]:.4f} '
              f'bestStep={best_step} gate={d["gate"]}',flush=True)
        transfer.save_state(latest,state(model,optimizer,rng,arm,step,best_rank,best_step,ident))
    if not (folder/'best.json').exists():
        raise RuntimeError('CARRY_NO_PROBES')
    best=json.loads((folder/'best.json').read_text(encoding='utf-8'))
    return {'best_step':best_step, 'gate':best['gate'],
            'dev_categories': {c:best['dev']['digit'][c]['rate'] for c in CATEGORIES},
            'replay_fraction':REPLAY_FRACTIONS[arm],
            'best_checkpoint':str(folder/'best.pt')}


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CARRY_CUDA_UNAVAILABLE')
    if (args.steps <= 0 or args.probe_every <= 0 or args.batch_size < 12 or
        args.eval_batch_size < 1 or args.train_per_group < 1 or
        args.dev_per_group < 1 or args.replay_per_group < 1 or
        args.cpu_threads < 1 or not 0 < args.learning_rate < 1 or
        not 0 < args.threshold <= 1 or not 0 < args.tolerance < 1):
        raise ValueError('CARRY_INVALID_OPTIONS')
    arms=args.arms.split(',')
    if not arms or len(set(arms))!=len(arms) or any(a not in ARMS for a in arms):
        raise ValueError('CARRY_INVALID_ARMS')
    torch.set_num_threads(args.cpu_threads)
    ck, source_hash=transfer.read_signed_checkpoint(args.signed_checkpoint)
    ident=identity(args,source_hash,ck)
    root=Path(args.output).expanduser().resolve()
    cfg=root/'config.json'
    if args.resume:
        if not cfg.is_file() or json.loads(cfg.read_text(encoding='utf-8'))!=ident:
            raise RuntimeError('CARRY_CONFIG_DRIFT')
    else:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('CARRY_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True,exist_ok=True)
        transfer.write_json(cfg,ident)
    current={p:make_dataset(p,args.seed,args.train_per_group if p=='train' else args.dev_per_group)
             for p in ('train','dev')}
    old_train,old_dev=old_datasets(ck,args.replay_per_group,args.dev_per_group)
    result={arm:run_arm(arm,args,ck,ident,current,old_train,old_dev,root)
            for arm in arms}
    if base.sha256(args.signed_checkpoint)!=source_hash:
        raise RuntimeError('CARRY_SOURCE_CHECKPOINT_MUTATED')
    transfer.write_json(root/'summary.json',{'schema':SCHEMA+'.summary',
                        'source_sha256':source_hash,'arms':result,'TEST':None,
                        'hybridlink_certified':False})
    print('ADDITION_CARRY_SUMMARY='+json.dumps({k:{'best_step':v['best_step'],
           'dev':v['dev_categories'],'signed_retention':v['gate']['old_rates']['signed'],
           'retention_min':v['gate']['retention_min'],'status':v['gate']['gate']}
           for k,v in result.items()},sort_keys=True),flush=True)
    print('ADDITION_SOURCE_IMMUTABLE=TRUE TEST_UNTOUCHED=TRUE HYBRIDLINK_UNCHANGED=TRUE',flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--signed-checkpoint',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--arms',default=','.join(ARMS))
    p.add_argument('--steps',type=int,default=2500,help='Total steps for each independent arm')
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--train-per-group',type=int,default=768)
    p.add_argument('--dev-per-group',type=int,default=128)
    p.add_argument('--replay-per-group',type=int,default=128)
    p.add_argument('--learning-rate',type=float,default=.0003)
    p.add_argument('--threshold',type=float,default=.8)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--seed',type=int,default=20261024)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':
    main()
