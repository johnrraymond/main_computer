#!/usr/bin/env python3
"""Warm-start a certified-source MLP interval specialist and measure OOD learning over time.

The Gaussian and existing incremental MLP experiments are never modified. The
old specialist supplies weights only: its original optimizer state was not saved,
so this is a NEW AdamW continuation, not an exact uninterrupted optimizer run.
No routing, OOD supervision, or promotion occurs here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_head_radius_growth import TRAIN_BANDS, OOD_BANDS, sampled_cards
from tools.nanojev_zero_line_gaussian_radius import make_cache
from tools.nanojev_zero_line_incremental_mlp_banks import (
    SCHEMA as PARENT_SCHEMA, IntervalMLPBank, atomic_json, atomic_pt,
    interval_report, interval_subset, logits_for_bank, parse_intervals,
)
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.zero-line-mlp-ood-extension.v1'


def validate_parent(parent, ladder_root, source_digest, interval, requested_seed):
    """Fail closed on a different base, training family, or interval."""
    parent = Path(parent).expanduser().resolve()
    config = json.loads((parent/'config.json').read_text(encoding='utf-8'))
    if config.get('schema') != PARENT_SCHEMA:
        raise ValueError('ZERO_OOD_PARENT_SCHEMA_MISMATCH')
    if config.get('source_sha256') != source_digest:
        raise ValueError('ZERO_OOD_SOURCE_MISMATCH')
    intervals = tuple(map(tuple,config['intervals']))
    if interval not in intervals:
        raise ValueError('ZERO_OOD_PARENT_INTERVAL_NOT_FOUND')
    if int(config['parameters']['seed']) != requested_seed:
        raise ValueError('ZERO_OOD_SEED_MISMATCH')
    if tuple(map(tuple,config['train_bands'])) != TRAIN_BANDS or tuple(map(tuple,config['ood_bands'])) != OOD_BANDS:
        raise ValueError('ZERO_OOD_FAMILY_BANDS_MISMATCH')
    bank_file = parent/f'bank_{interval[0]}_{interval[1]}'/'specialist.pt'
    if not bank_file.is_file():
        raise FileNotFoundError('ZERO_OOD_SPECIALIST_NOT_FOUND')
    state = torch.load(bank_file, map_location='cpu', weights_only=True)
    if state.get('schema') != PARENT_SCHEMA+'.specialist' or state.get('source_sha256') != source_digest:
        raise ValueError('ZERO_OOD_SPECIALIST_SOURCE_MISMATCH')
    if tuple(state['interval']) != interval:
        raise ValueError('ZERO_OOD_SPECIALIST_INTERVAL_MISMATCH')
    result_file = parent/f'bank_{interval[0]}_{interval[1]}'/'result.json'
    parent_result = json.loads(result_file.read_text(encoding='utf-8'))
    return config, state, parent_result, bank_file


def make_probe(bank, train, dev, ood, low, high, step, loss, min_rate, min_cell):
    """OOD observed for science, not used for gradients or checkpoint selection."""
    summaries = {}
    for name, cache in (('train',train),('dev',dev),('ood',ood)):
        summary = interval_report(logits_for_bank(bank,cache),cache,low,high,min_rate,min_cell)
        summaries[name] = {'accuracy':summary['accuracy'], 'correct':summary['correct'],
                           'cards':summary['cards'], 'certified':summary['certified'],
                           'failing_cells':summary['failing_cells']}
    return {'step_added':step, 'loss':loss, **summaries}


def extension_step(bank, optimizer, train, rng, high, batch_size):
    bank.train()
    indices = torch.tensor([rng.randrange(len(train['target'])) for _ in range(batch_size)],
                           device=train['target'].device)
    logits,z = bank(train['features'][indices])
    y=train['target'][indices]
    sl=F.cross_entropy(logits[:,:3],y.sign()+1)
    dl=F.cross_entropy(logits[:,3:].reshape(-1,7,10).transpose(1,2),target_digits(y))
    coord=F.smooth_l1_loss(z,y.float()/high)
    loss=sl+2.*dl+.5*coord
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    nn.utils.clip_grad_norm_(bank.parameters(),1.)
    optimizer.step()
    return float(loss.detach())


def _history_report(output, config, baseline, observations):
    result = {'schema':SCHEMA,'parameters':config,
              'original_750_3000_report':baseline,
              'observations':observations,
              'final':observations[-1],
              'ood_used_for_training':False,'ood_used_for_selection':False,
              'backbone_frozen':True,'gaussian_untouched':True,'router_untouched':True,
              'optimizer_continuation':'fresh_adamw_warm_start_from_specialist_weights'}
    atomic_json(output/'result.json',result)
    return result


def run(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_OOD_CUDA_UNAVAILABLE')
    if (args.additional_steps < 1 or args.probe_every < 1 or args.batch_size < 4 or
        args.eval_batch_size < 1 or args.train_per_cell < 1 or args.dev_per_cell < 1 or
        args.ood_per_cell < 1 or args.cpu_threads < 1 or not 0 < args.lr < 1 or
        args.min_cell_count < 1 or not 0 < args.min_cell_rate <= 1):
        raise ValueError('ZERO_OOD_BAD_ARGUMENTS')
    parsed = parse_intervals(args.interval)
    if len(parsed)!=1:
        raise ValueError('ZERO_OOD_SINGLE_INTERVAL_REQUIRED')
    interval=parsed[0];low,high=interval
    output=Path(args.output).expanduser().resolve()
    ladder_root=Path(args.ladder_output).expanduser().resolve()
    parent=Path(args.mlp_output).expanduser().resolve()
    if output in (parent,ladder_root) or any(output in root.parents or root in output.parents for root in (ladder_root,parent)):
        raise RuntimeError('ZERO_OOD_PATH_OVERLAP')
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise FileExistsError('ZERO_OOD_OUTPUT_EXISTS_USE_RESUME_OR_NEW_DIRECTORY')
    torch.set_num_threads(args.cpu_threads)
    base,source=load_certified_model(ladder_root,args.device)
    source_digest=source['checkpoint_sha256']
    for p in base.parameters():p.requires_grad_(False)
    conf,parent_state,parent_report,source_specialist=validate_parent(parent,ladder_root,source_digest,interval,args.seed)
    # Keep exactly the original TRAIN/DEV/OOD cards, not resampled populations.
    train=interval_subset(make_cache(base,sampled_cards(high,TRAIN_BANDS,args.seed,'train',args.train_per_cell),
                                     args.device,args.eval_batch_size),low,high)
    dev=interval_subset(make_cache(base,sampled_cards(high,TRAIN_BANDS,args.seed,'dev',args.dev_per_cell),
                                   args.device,args.eval_batch_size),low,high)
    ood=interval_subset(make_cache(base,sampled_cards(high,OOD_BANDS,args.seed,'ood',args.ood_per_cell),
                                   args.device,args.eval_batch_size),low,high)
    # Enforce same evaluation/sampling spec as the original 3,000-step experiment.
    p=conf['parameters']
    for name in ('train_per_cell','dev_per_cell','ood_per_cell','min_cell_count','min_cell_rate'):
        if getattr(args,name) != p[name]:
            raise ValueError(f'ZERO_OOD_PARENT_SETTING_MISMATCH:{name}')
    width=int(base.query.shape[0])
    hidden=(parent_report.get('parameters') or {}).get('mlp_hidden')
    bank=IntervalMLPBank(width,low,high,hidden=hidden).to(args.device)
    bank.load_state_dict(parent_state['bank'],strict=True)
    config={'schema':SCHEMA,'source_sha256':source_digest,'source_specialist_sha256':sha256(source_specialist),
            'parent_output':str(parent),'interval':interval,'seed':args.seed,
            'additional_steps':args.additional_steps,'probe_every':args.probe_every,
            'batch_size':args.batch_size,'lr':args.lr,
            'train_per_cell':args.train_per_cell,'dev_per_cell':args.dev_per_cell,
            'ood_per_cell':args.ood_per_cell,'min_cell_count':args.min_cell_count,
            'min_cell_rate':args.min_cell_rate,
            'starting_training_steps':p['steps_per_bank'],
            'optimizer_continuation':'fresh_adamw_warm_start_from_specialist_weights'}
    optimizer=torch.optim.AdamW(bank.parameters(),lr=args.lr,weight_decay=0.)
    rng=random.Random(args.seed+73)
    baseline={'specialist_dev':parent_report['specialist']['dev']['accuracy'],
              'specialist_ood':parent_report['specialist']['ood']['accuracy']}
    checkpoint=output/'latest.pt'
    if args.resume:
        stored_conf=json.loads((output/'config.json').read_text(encoding='utf-8'))
        if stored_conf!=json.loads(json.dumps(config)):
            raise ValueError('ZERO_OOD_RESUME_CONFIG_MISMATCH')
        state=torch.load(checkpoint, map_location=args.device,weights_only=True)
        if state.get('schema')!=SCHEMA+'.checkpoint' or state['source_sha256']!=source_digest:
            raise ValueError('ZERO_OOD_RESUME_SOURCE_MISMATCH')
        bank.load_state_dict(state['bank'],strict=True)
        optimizer.load_state_dict(state['optimizer'])
        rng.setstate(state['rng'])
        observations=state['observations']
        start=int(state['step_added'])
    else:
        output.mkdir(parents=True,exist_ok=True)
        atomic_json(output/'config.json',config)
        initial=make_probe(bank,train,dev,ood,low,high,0,None,args.min_cell_rate,args.min_cell_count)
        tolerance=1e-9
        if abs(initial['dev']['accuracy']-baseline['specialist_dev'])>tolerance or abs(initial['ood']['accuracy']-baseline['specialist_ood'])>tolerance:
            raise RuntimeError('ZERO_OOD_BASELINE_REPRODUCTION_FAILED')
        observations=[initial]
        start=0
        _history_report(output,config,baseline,observations)
        print('ZERO_MLP_OOD_PROBE='+json.dumps(initial,sort_keys=True),flush=True)
    for step in range(start+1,args.additional_steps+1):
        loss=extension_step(bank,optimizer,train,rng,high,args.batch_size)
        if step%args.probe_every and step!=args.additional_steps:
            continue
        current=make_probe(bank,train,dev,ood,low,high,step,loss,args.min_cell_rate,args.min_cell_count)
        observations.append(current)
        # Save resumable training state, separately from the original specialist.
        atomic_pt(checkpoint,{'schema':SCHEMA+'.checkpoint','source_sha256':source_digest,
                              'step_added':step,'bank':bank.state_dict(),
                              'optimizer':optimizer.state_dict(),'rng':rng.getstate(),
                              'observations':observations})
        _history_report(output,config,baseline,observations)
        print('ZERO_MLP_OOD_PROBE='+json.dumps(current,sort_keys=True),flush=True)
    if sha256(source['checkpoint'])!=source_digest or sha256(source_specialist)!=config['source_specialist_sha256']:
        raise RuntimeError('ZERO_OOD_PROTECTED_SOURCE_MUTATED')
    # OOD observations are diagnostic; NO selection of best checkpoint on OOD.
    print('ZERO_MLP_OOD_SUMMARY='+json.dumps({'initial_ood':observations[0]['ood']['accuracy'],
        'final_ood':observations[-1]['ood']['accuracy'],
        'final_dev':observations[-1]['dev']['accuracy'],
        'steps_added':observations[-1]['step_added'],
        'source_immutable':True,'optimizer_reset_at_start':True},sort_keys=True),flush=True)
    return observations


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ladder-output',required=True)
    p.add_argument('--mlp-output',required=True,help='Original MLP output with bank_16_32/specialist.pt')
    p.add_argument('--output',required=True,help='New output directory; never overwrites original')
    p.add_argument('--interval',default='16:32')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--additional-steps',type=int,default=6000)
    p.add_argument('--probe-every',type=int,default=500)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--train-per-cell',type=int,default=24)
    p.add_argument('--dev-per-cell',type=int,default=12)
    p.add_argument('--ood-per-cell',type=int,default=12)
    p.add_argument('--min-cell-count',type=int,default=6)
    p.add_argument('--min-cell-rate',type=float,default=.8)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))

if __name__=='__main__':
    main()
