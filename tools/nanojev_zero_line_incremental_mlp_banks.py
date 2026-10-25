#!/usr/bin/env python3
"""Independent incremental MLP interval-bank experiment; Gaussian files untouched.

Matches the established Gaussian incremental harness in data families, exact sign
and digit metrics, coordinate auxiliary supervision, protected incumbent gating,
and interval promotion. Specialist differs only by removing the Gaussian RBF
basis and matching trainable parameter budget with a conventional MLP readout.
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
from tools.nanojev_zero_line_head_radius_growth import TRAIN_BANDS, OOD_BANDS, sampled_cards
from tools.nanojev_zero_line_gaussian_radius import make_cache, infer
from tools.nanojev_zero_line_ladder import deck
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256

SCHEMA = 'nanojev.zero-line-incremental-mlp-interval-banks.v1'
DEFAULT_INTERVALS = '3:15,16:32,33:64,65:128'


def parse_intervals(value: str):
    result = []
    for raw in value.split(','):
        pair = raw.strip().split(':')
        if len(pair) != 2:
            raise ValueError('ZERO_MLP_INTERVAL_INVALID')
        try:
            low, high = map(int, pair)
        except ValueError as exc:
            raise ValueError('ZERO_MLP_INTERVAL_INVALID') from exc
        if not (2 <= low <= high <= 128):
            raise ValueError('ZERO_MLP_INTERVAL_OUT_OF_RANGE')
        if result and low != result[-1][1] + 1:
            raise ValueError('ZERO_INTERVALS_NOT_CONTIGUOUS')
        result.append((low, high))
    if not result:
        raise ValueError('ZERO_INTERVALS_EMPTY')
    return tuple(result)


def atomic_json(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(content, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def atomic_pt(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    torch.save(data, temporary)
    os.replace(temporary, path)


class IntervalMLPBank(nn.Module):
    """Parameter-matched MLP specialist for signed interval [-hi,-lo] U [lo,hi].

    Identical learned residual coordinate auxiliary head and training loss as
    the Gaussian incremental specialist, but without Gaussian response bases.
    Neither target nor interval identifier is supplied to forward/inference.
    """
    def __init__(self, width: int, lower: int, upper: int, hidden: int | None = None):
        super().__init__()
        if not (2 <= lower <= upper) or width < 1:
            raise ValueError('ZERO_MLP_INVALID_GEOMETRY')
        self.lower, self.upper = lower, upper
        # Match Gaussian specialist: width + 1 learned z + 32 fixed basis cells,
        # 256 output-hidden units. Both architectures share LayerNorm and z MLP.
        gaussian_readout_count = (width + 33 + 1) * 256 + (256 + 1) * 73
        if hidden is None:
            hidden = round((gaussian_readout_count - 73) / (width + 75))
        if hidden < 1:
            raise ValueError('ZERO_MLP_BAD_HIDDEN')
        self.hidden = hidden
        self.norm = nn.LayerNorm(width)
        self.coordinate = nn.Sequential(nn.Linear(width, 128), nn.GELU(), nn.Linear(128, 1))
        self.output = nn.Sequential(nn.Linear(width + 1, hidden), nn.GELU(),
                                    nn.Linear(hidden, 73))

    def forward(self, features):
        x = self.norm(features)
        z = self.coordinate(x)
        logits = self.output(torch.cat((x, z), dim=-1))
        return logits, z.squeeze(-1)


class LearnedIntervalGate(nn.Module):
    """Predict use-new-bank vs retain-incumbent from backbone features only."""
    def __init__(self, width):
        super().__init__()
        self.network = nn.Sequential(nn.LayerNorm(width), nn.Linear(width, 128),
                                     nn.GELU(), nn.Linear(128, 1))
        nn.init.zeros_(self.network[-1].weight)
        nn.init.constant_(self.network[-1].bias, -3.)

    def forward(self, features):
        return self.network(features).squeeze(-1)


def exact_mask(logits, truth):
    sign = logits[:, :3].argmax(-1).long() - 1
    digits = logits[:, 3:].reshape(-1, 7, 10).argmax(-1).long()
    powers = digits.new_tensor([10 ** i for i in range(7)])
    value = sign * (digits * powers).sum(-1)
    return (value == truth) & (sign == truth.sign()) & (digits == target_digits(truth)).all(-1)


def percentage(logits, truth):
    if not len(truth):
        raise RuntimeError('ZERO_BANK_NO_EVAL_CARDS')
    return int(exact_mask(logits, truth).sum().item()) / len(truth)


def subset(cache, indices):
    idx = torch.as_tensor(indices, dtype=torch.long, device=cache['target'].device)
    return {'features':cache['features'][idx], 'original':cache['original'][idx],
            'target':cache['target'][idx], 'operands':cache['operands'][idx.cpu()],
            'bands':[cache['bands'][int(i)] for i in idx.cpu().tolist()] if cache['bands'] is not None else None}


def interval_subset(cache, low, high):
    targets = cache['target'].detach().cpu().tolist()
    return subset(cache, [i for i,t in enumerate(targets) if low <= abs(t) <= high])


def logits_for_bank(bank, cache, batch_size=1024):
    pieces = []
    with torch.inference_mode():
        bank.eval()
        for j in range(0, len(cache['target']), batch_size):
            pieces.append(bank(cache['features'][j:j+batch_size])[0])
    return torch.cat(pieces, dim=0)


class IncrementalComposite:
    def __init__(self, source_operand_radius=127):
        self.source_operand_radius = source_operand_radius
        self.promoted = []                    # (bank, gate, low, high), immutable after promotion

    @torch.inference_mode()
    def predict(self, cache, *, candidate=None, guarded=False):
        logits = cache['original'].clone()
        features = cache['features']
        active = [*self.promoted]
        if candidate is not None:
            active.append(candidate)
        for bank, gate, _low, _high in active:
            bank.eval(); gate.eval()
            new_logits, _ = bank(features)
            choose = gate(features) > 0.     # no target or residual at inference
            logits = torch.where(choose[:,None], new_logits, logits)
        if guarded and active:
            # Measurable structural protection of exactly the old operand domain;
            # no sum/difference/residual is calculated for this guard.
            operands = cache['operands'].to(logits.device)
            historical = operands.abs().amax(dim=-1) <= self.source_operand_radius
            logits = torch.where(historical[:,None], cache['original'], logits)
        return logits


def interval_report(logits, cache, low, high, min_rate=.8, min_cell=6):
    truth = cache['target']
    good = exact_mask(logits, truth).cpu().tolist()
    y = truth.cpu().tolist()
    if cache['bands'] is None:
        raise ValueError('ZERO_BANK_BAND_METADATA_MISSING')
    cells = {}
    for (delta, passed, (band, orient)) in zip(y, good, cache['bands']):
        if not low <= abs(delta) <= high:
            continue
        name = f'{delta}:{band}:{orient}'
        if name not in cells:
            cells[name] = {'count':0, 'correct':0}
        cells[name]['count'] += 1
        cells[name]['correct'] += int(passed)
    for row in cells.values():
        row['accuracy'] = row['correct'] / row['count']
    missing = []
    for delta in [*range(-high, -low+1), *range(low, high+1)]:
        for band in range(4):
            for orient in (-1,1):
                name = f'{delta}:{band}:{orient}'
                if (name not in cells or cells[name]['count'] < min_cell or
                        cells[name]['accuracy'] < min_rate):
                    missing.append(name)
    count = sum(row['count'] for row in cells.values())
    correct = sum(row['correct'] for row in cells.values())
    return {'interval':[low, high], 'cards':count, 'correct':correct,
            'accuracy':correct / count if count else 0., 'certified':not missing,
            'failing_cells':len(missing), 'sample_failing_cells':missing[:12],
            'minimum_cell_count':min_cell, 'minimum_cell_accuracy':min_rate}


def train_expert(bank, train, dev, low, high, *, steps, batch_size, lr, probe_every, seed):
    rng = random.Random(seed)
    optimizer = torch.optim.AdamW(bank.parameters(), lr=lr, weight_decay=0.)
    history = []
    bank.train()
    for step in range(1, steps+1):
        ids = torch.tensor([rng.randrange(len(train['target'])) for _ in range(batch_size)],
                           device=train['target'].device)
        x = train['features'][ids]
        y = train['target'][ids]
        logits, z = bank(x)
        sl = F.cross_entropy(logits[:, :3], y.sign()+1)
        dl = F.cross_entropy(logits[:, 3:].reshape(-1,7,10).transpose(1,2),target_digits(y))
        coord = F.smooth_l1_loss(z, y.float() / high)
        loss = sl + 2.*dl + .5*coord
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(bank.parameters(), 1.)
        optimizer.step()
        if step % probe_every and step != steps:
            continue
        tr = percentage(logits_for_bank(bank,train),train['target'])
        dv = percentage(logits_for_bank(bank,dev),dev['target'])
        item = {'step':step, 'loss':float(loss.detach()), 'train_accuracy':tr,
                'dev_accuracy':dv, 'coordinate_loss':float(coord.detach())}
        history.append(item)
        print(f'ZERO_MLP_BANK interval={low}:{high} step={step}/{steps} '
              f'loss={item["loss"]:.4f} TRAIN={tr:.4f} DEV={dv:.4f}',flush=True)
    return history


def train_gate(gate, bank, incumbent, old, new, *, steps, batch_size, lr, seed, protected=None, protected_fraction=.5):
    """Distill choices from TRAIN correctness; do not route using labels at inference.

    Historical cards are always negative (keep the certified model); for new
    cards switch only when new bank fixes an incumbent error. This trains the
    *choice*, not the bank, and makes routing failures independently measurable.
    """
    with torch.inference_mode():
        bank.eval()
        old_inc = exact_mask(incumbent.predict(old),old['target'])
        new_inc = exact_mask(incumbent.predict(new),new['target'])
        new_bank = exact_mask(logits_for_bank(bank,new),new['target'])
        labels = new_bank & ~new_inc
        positive = labels.nonzero().reshape(-1).tolist()
        negative = (~labels).nonzero().reshape(-1).tolist()
    result = {'improvable_train_cards':len(positive), 'new_train_cards':len(labels),
              'historical_incumbent_errors':int((~old_inc).sum())}
    if not positive:
        return result | {'status':'NO_FIXABLE_TRAIN_EXAMPLES', 'steps':0}
    rng = random.Random(seed)
    opt = torch.optim.AdamW(gate.parameters(),lr=lr,weight_decay=0.)
    for _step in range(1, steps+1):
        old_n = max(1,round(batch_size*protected_fraction))
        new_n = batch_size-old_n
        pos_n = max(1,new_n//2) if negative else new_n
        neg_n = new_n-pos_n
        protected_n=old_n//2 if protected is not None and len(protected['target']) else 0
        original_n=old_n-protected_n
        op = torch.tensor([rng.randrange(len(old['target'])) for _ in range(original_n)],
                          device=old['features'].device)
        if protected_n:
            protected_ids=torch.tensor([rng.randrange(len(protected['target']))
                                        for _ in range(protected_n)],
                                       device=protected['features'].device)
        pp = torch.tensor([positive[rng.randrange(len(positive))] for _ in range(pos_n)],
                          device=new['features'].device)
        np = torch.tensor([negative[rng.randrange(len(negative))] for _ in range(neg_n)],
                          device=new['features'].device, dtype=torch.long)
        parts=[old['features'][op]]
        if protected_n: parts.append(protected['features'][protected_ids])
        feats = torch.cat(parts+[new['features'][pp],new['features'][np]],0)
        target = torch.cat([torch.zeros(old_n,device=feats.device),
                            torch.ones(pos_n,device=feats.device),
                            torch.zeros(neg_n,device=feats.device)])
        loss = F.binary_cross_entropy_with_logits(gate(feats),target)
        opt.zero_grad(set_to_none=True);loss.backward()
        nn.utils.clip_grad_norm_(gate.parameters(),1.);opt.step()
    return result | {'status':'TRAINED', 'steps':steps, 'final_loss':float(loss.detach())}


def protected_regression_audit(composite, train_or_dev, intervals, candidate):
    """Require zero previously correct predictions to become incorrect.

    Always includes the near-zero 0..2 neighborhood at novel operand scales,
    plus every previously promoted interval. Never trains from DEV/OOD.
    """
    groups = [(0,2)] + [(lo,hi) for _,_,lo,hi in composite.promoted]
    rows=[]
    for lo,hi in groups:
        targets=train_or_dev['target'].detach().cpu().tolist()
        indices=[i for i,y in enumerate(targets) if lo <= abs(y) <= hi]
        if not indices:
            raise RuntimeError('ZERO_MLP_INTERVAL_PROTECTED_EVAL_MISSING')
        block=subset(train_or_dev,indices)
        before=exact_mask(composite.predict(block,guarded=False),block['target'])
        after=exact_mask(composite.predict(block,candidate=candidate,guarded=False),block['target'])
        changed=(before & ~after)
        rows.append({'interval':[lo,hi],'before':float(before.float().mean()),
                     'after':float(after.float().mean()),
                     'newly_wrong':int(changed.sum()), 'cards':len(block['target'])})
    return {'regions':rows,'no_regressions':all(r['newly_wrong']==0 for r in rows)}


def report_one(composite, cache, low, high, threshold, cell_count, candidate=None):
    raw = composite.predict(cache,candidate=candidate,guarded=False)
    guarded = composite.predict(cache,candidate=candidate,guarded=True)
    return {'unguarded':interval_report(raw,cache,low,high,threshold,cell_count),
            'guarded':interval_report(guarded,cache,low,high,threshold,cell_count)}


def evaluate_retention(composite, old, candidate=None):
    return {'unguarded':percentage(composite.predict(old,candidate=candidate,guarded=False),old['target']),
            'guarded':percentage(composite.predict(old,candidate=candidate,guarded=True),old['target'])}


def run(args):
    intervals = parse_intervals(args.intervals)
    if (args.device=='cuda' and not torch.cuda.is_available()):
        raise RuntimeError('ZERO_MLP_INTERVAL_CUDA_UNAVAILABLE')
    if (args.steps_per_bank<1 or args.router_steps<1 or args.probe_every<1 or
        args.train_per_cell<1 or args.dev_per_cell<1 or args.ood_per_cell<1 or
        args.batch_size<4 or args.eval_batch_size<1 or args.cpu_threads<1 or
        args.min_cell_count<1 or not 0 < args.lr < 1 or
        not 0 < args.min_cell_rate <= 1):
        raise ValueError('ZERO_MLP_INTERVAL_INVALID_OPTIONS')
    output=Path(args.output).expanduser().resolve()
    source_root=Path(args.ladder_output).expanduser().resolve()
    if (output==source_root or output in source_root.parents or source_root in output.parents):
        raise RuntimeError('ZERO_MLP_INTERVAL_SOURCE_OUTPUT_OVERLAP')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('ZERO_MLP_INTERVAL_OUTPUT_EXISTS')
    torch.set_num_threads(args.cpu_threads)
    model, source = load_certified_model(source_root,args.device)
    for parameter in model.parameters():parameter.requires_grad_(False)
    width=int(model.query.shape[0])
    old=make_cache(model,deck(127),args.device,args.eval_batch_size)
    if percentage(old['original'],old['target']) != 1.:
        raise RuntimeError('ZERO_MLP_INTERVAL_CERTIFICATE_RUNTIME_MASTERY_MISMATCH')
    max_delta=max(upper for _,upper in intervals)
    train=make_cache(model,sampled_cards(max_delta,TRAIN_BANDS,args.seed,'train',args.train_per_cell),
                     args.device,args.eval_batch_size)
    dev=make_cache(model,sampled_cards(max_delta,TRAIN_BANDS,args.seed,'dev',args.dev_per_cell),
                   args.device,args.eval_batch_size)
    ood=make_cache(model,sampled_cards(max_delta,OOD_BANDS,args.seed,'ood',args.ood_per_cell),
                   args.device,args.eval_batch_size)
    source_digest=source['checkpoint_sha256']
    conf={'schema':SCHEMA,'source_sha256':source_digest,'source_step':source['step'],
          'script_sha256':sha256(__file__),'intervals':intervals,
          'architecture':'mlp_no_gaussian_basis',
          'train_bands':TRAIN_BANDS,'ood_bands':OOD_BANDS,
          'parameters':{key:value for key,value in vars(args).items()
                        if key not in ('output','ladder_output','device','gaussian_output','phase')}}
    output.mkdir(parents=True,exist_ok=True)
    atomic_json(output/'config.json',conf)
    composite=IncrementalComposite()
    all_history=[]
    for index,(lower,upper) in enumerate(intervals):
        part=output/f'bank_{lower}_{upper}'
        part.mkdir()
        tr=interval_subset(train,lower,upper)
        dv=interval_subset(dev,lower,upper)
        od=interval_subset(ood,lower,upper)
        if min(len(tr['target']),len(dv['target']),len(od['target'])) == 0:
            raise RuntimeError('ZERO_MLP_INTERVAL_MISSING_POPULATION')
        torch.manual_seed(args.seed+index*17)
        bank=IntervalMLPBank(width,lower,upper).to(args.device)
        before_dev=report_one(composite,dv,lower,upper,args.min_cell_rate,args.min_cell_count)
        before_ood=report_one(composite,od,lower,upper,args.min_cell_rate,args.min_cell_count)
        history=train_expert(bank,tr,dv,lower,upper,steps=args.steps_per_bank,
                             batch_size=args.batch_size,lr=args.lr,
                             probe_every=args.probe_every,seed=args.seed+index*47)
        bank_dev=interval_report(logits_for_bank(bank,dv),dv,lower,upper,
                                 args.min_cell_rate,args.min_cell_count)
        bank_ood=interval_report(logits_for_bank(bank,od),od,lower,upper,
                                 args.min_cell_rate,args.min_cell_count)
        atomic_pt(part/'specialist.pt',{'schema':SCHEMA+'.specialist','source_sha256':source_digest,
                                       'interval':[lower,upper], 'bank':bank.state_dict(),
                                       'training_history':history})
        gate=LearnedIntervalGate(width).to(args.device)
        protected_indices=[i for i,y in enumerate(train['target'].detach().cpu().tolist())
                           if abs(y)<=2 or any(lo<=abs(y)<=hi for _,_,lo,hi in composite.promoted)]
        protected_train=subset(train,protected_indices)
        routing_train=train_gate(gate,bank,composite,old,tr,
                                 steps=args.router_steps,batch_size=args.batch_size,
                                 lr=args.router_lr,seed=args.seed+index*157,
                                 protected=protected_train)
        candidate=(bank,gate,lower,upper)
        after_dev=report_one(composite,dv,lower,upper,args.min_cell_rate,args.min_cell_count,candidate)
        after_ood=report_one(composite,od,lower,upper,args.min_cell_rate,args.min_cell_count,candidate)
        retention=evaluate_retention(composite,old,candidate)
        protected_dev=protected_regression_audit(composite,dev,intervals,candidate)
        protected_ood=protected_regression_audit(composite,ood,intervals,candidate)
        old_retention=evaluate_retention(composite,old)
        with torch.inference_mode():
            gate_new_rate=float((gate(dv['features'])>0).float().mean())
            gate_old_rate=float((gate(old['features'])>0).float().mean())
        # Only accept if all signed interval cells succeed at both magnitudes;
        # original 65,025 cards stay perfect even without the protective guard.
        # All previously promoted banks remain immutable.
        eligible=(protected_dev['no_regressions'] and protected_ood['no_regressions'] and
                  after_dev['unguarded']['certified'] and
                  after_ood['unguarded']['certified'] and
                  retention['unguarded']==1. and retention['guarded']==1. and
                  after_dev['unguarded']['accuracy']>before_dev['unguarded']['accuracy'] and
                  after_ood['unguarded']['accuracy']>=before_ood['unguarded']['accuracy'])
        audit={'interval':[lower,upper],'index':index,
               'parameters':{'specialist':sum(p.numel() for p in bank.parameters()),
                             'mlp_hidden':bank.hidden,
                             'router':sum(p.numel() for p in gate.parameters())},
               'trained_cards':len(tr['target']),
               'dev_cards':len(dv['target']),'ood_cards':len(od['target']),
               'specialist':{'dev':bank_dev,'ood':bank_ood},
               'incumbent':{'dev':before_dev,'ood':before_ood,'retention':old_retention},
               'candidate':{'dev':after_dev,'ood':after_ood,'retention':retention,
                            'protected_dev':protected_dev,'protected_ood':protected_ood},
               'router':routing_train | {'dev_choose_new_rate':gate_new_rate,
                                        'historical_choose_new_rate':gate_old_rate},
               'eligible':bool(eligible),
               'bank_architecture':'mlp_no_gaussian_basis',
               'notes':'Specialist is evaluated with oracle isolation only for diagnosis; candidate and promotion use learned routing only.'}
        atomic_json(part/'result.json',audit)
        atomic_pt(part/'candidate.pt',{'schema':SCHEMA+'.candidate',
                                       'source_sha256':source_digest,'interval':[lower,upper],
                                       'bank':bank.state_dict(),'gate':gate.state_dict(),
                                       'eligible':bool(eligible),'audit':audit})
        if eligible:
            composite.promoted.append(candidate)
            atomic_pt(part/'promoted.pt',{'schema':SCHEMA+'.promoted','source_sha256':source_digest,
                                         'interval':[lower,upper],'bank':bank.state_dict(),
                                         'gate':gate.state_dict(),'prior_intervals':
                                         [[lo,hi] for _,_,lo,hi in composite.promoted[:-1]]})
        print('ZERO_MLP_INTERVAL_RESULT='+json.dumps({'interval':[lower,upper],
              'specialist_dev':bank_dev['accuracy'],'specialist_ood':bank_ood['accuracy'],
              'routed_dev':after_dev['unguarded']['accuracy'],
              'routed_ood':after_ood['unguarded']['accuracy'],
              'historical_unguarded':retention['unguarded'],
              'eligible':bool(eligible)},sort_keys=True),flush=True)
        all_history.append(audit)
        atomic_json(output/'result.json',{'schema':SCHEMA,'source':source,
             'mastered_cards':len(old['target']),
             'promoted_intervals':[[lo,hi] for _,_,lo,hi in composite.promoted],
             'history':all_history,'test_untouched':True,'source_immutable':True})
    if sha256(source['checkpoint'])!=source_digest:
        raise RuntimeError('ZERO_MLP_INTERVAL_CERTIFIED_CHECKPOINT_MUTATED')
    print('ZERO_MLP_INTERVAL_SUMMARY='+json.dumps({'promoted_intervals':
          [[lo,hi] for _,_,lo,hi in composite.promoted],
          'results':[{ 'interval':h['interval'],
                       'isolated_dev':h['specialist']['dev']['accuracy'],
                       'routed_dev':h['candidate']['dev']['unguarded']['accuracy'],
                       'retention':h['candidate']['retention']['unguarded'],
                       'promoted':h['eligible']} for h in all_history]},sort_keys=True),flush=True)
    print('ZERO_MLP_SOURCE_IMMUTABLE=TRUE BASE_FROZEN=TRUE MLP_BANKS_INDEPENDENT=TRUE TEST_UNTOUCHED=TRUE',flush=True)
    if args.gaussian_output:
        comparison=compare_outputs(output, Path(args.gaussian_output))
        print('ZERO_MLP_GAUSSIAN_COMPARISON='+json.dumps(comparison['summary'],sort_keys=True),flush=True)
    return all_history


# Apples-to-apples comparison: fail closed rather than comparing incompatible runs.
MATCHED_ARGUMENTS = ('intervals','steps_per_bank','router_steps','probe_every',
                     'batch_size','eval_batch_size','train_per_cell','dev_per_cell',
                     'ood_per_cell','min_cell_count','min_cell_rate','lr',
                     'router_lr','seed')


def _load_research_output(root):
    root=Path(root).expanduser().resolve()
    conf=json.loads((root/'config.json').read_text(encoding='utf-8'))
    report=json.loads((root/'result.json').read_text(encoding='utf-8'))
    return root,conf,report


def compare_outputs(mlp_dir, gaussian_dir):
    mlp_root,mlp_conf,mlp=_load_research_output(mlp_dir)
    gauss_root,gauss_conf,gauss=_load_research_output(gaussian_dir)
    if mlp_conf.get('schema')!=SCHEMA or gauss_conf.get('schema')!='nanojev.zero-line-incremental-interval-banks.v1':
        raise ValueError('ZERO_MLP_COMPARISON_SCHEMA_MISMATCH')
    if mlp_conf.get('source_sha256')!=gauss_conf.get('source_sha256'):
        raise ValueError('ZERO_MLP_COMPARISON_SOURCE_MISMATCH')
    if tuple(map(tuple, mlp_conf['intervals'])) != tuple(map(tuple, gauss_conf['intervals'])):
        raise ValueError('ZERO_MLP_COMPARISON_INTERVAL_MISMATCH')
    if mlp_conf.get('train_bands')!=gauss_conf.get('train_bands') or mlp_conf.get('ood_bands')!=gauss_conf.get('ood_bands'):
        raise ValueError('ZERO_MLP_COMPARISON_BANDS_MISMATCH')
    for key in MATCHED_ARGUMENTS:
        if mlp_conf['parameters'].get(key) != gauss_conf['parameters'].get(key):
            raise ValueError(f'ZERO_MLP_COMPARISON_PARAMETER_MISMATCH:{key}')
    mlp_rows=mlp['history'];gauss_rows=gauss['history']
    if len(mlp_rows)!=len(gauss_rows):
        raise ValueError('ZERO_MLP_COMPARISON_INCOMPLETE')
    rows=[]
    for a,b in zip(mlp_rows,gauss_rows):
        if a['interval']!=b['interval'] or any(a[k]!=b[k] for k in ('trained_cards','dev_cards','ood_cards')):
            raise ValueError('ZERO_MLP_COMPARISON_POPULATION_MISMATCH')
        def metric(entry):
            return {'isolated_dev':entry['specialist']['dev']['accuracy'],
                    'isolated_ood':entry['specialist']['ood']['accuracy'],
                    'routed_dev':entry['candidate']['dev']['unguarded']['accuracy'],
                    'routed_ood':entry['candidate']['ood']['unguarded']['accuracy'],
                    'retained_original':entry['candidate']['retention']['unguarded'],
                    'isolated_dev_certified':entry['specialist']['dev']['certified'],
                    'isolated_ood_certified':entry['specialist']['ood']['certified'],
                    'promoted':bool(entry['eligible']),
                    'specialist_parameters':entry['parameters']['specialist'],
                    'gate_parameters':entry['parameters']['router']}
        x,y=metric(a),metric(b)
        rows.append({'interval':a['interval'],'mlp':x,'gaussian':y,
                     'mlp_minus_gaussian':{k:x[k]-y[k] for k in
                         ('isolated_dev','isolated_ood','routed_dev','routed_ood','retained_original')},
                     'parameter_ratio':x['specialist_parameters']/y['specialist_parameters']})
    comparison={'schema':'nanojev.zero-line-incremental-mlp-vs-gaussian.v1',
                'source_sha256':mlp_conf['source_sha256'],
                'mlp_output':str(mlp_root),'gaussian_output':str(gauss_root),
                'matched_arguments':{k:mlp_conf['parameters'][k] for k in MATCHED_ARGUMENTS},
                'rows':rows,'summary':{'intervals_compared':len(rows),
                    'mlp_promoted':sum(r['mlp']['promoted'] for r in rows),
                    'gaussian_promoted':sum(r['gaussian']['promoted'] for r in rows),
                    'comparison_verified':True}}
    atomic_json(mlp_root/'comparison.json',comparison)
    return comparison


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('train','compare'),default='train')
    p.add_argument('--ladder-output')
    p.add_argument('--output',required=True)
    p.add_argument('--gaussian-output',help='Existing Gaussian INCREMENTAL output folder for verified comparison')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--intervals',default=DEFAULT_INTERVALS)
    p.add_argument('--steps-per-bank',type=int,default=750)
    p.add_argument('--router-steps',type=int,default=400)
    p.add_argument('--probe-every',type=int,default=150)
    p.add_argument('--batch-size',type=int,default=128)
    p.add_argument('--eval-batch-size',type=int,default=512)
    p.add_argument('--train-per-cell',type=int,default=24)
    p.add_argument('--dev-per-cell',type=int,default=12)
    p.add_argument('--ood-per-cell',type=int,default=12)
    p.add_argument('--min-cell-count',type=int,default=6)
    p.add_argument('--min-cell-rate',type=float,default=.8)
    p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--router-lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=20261025)
    p.add_argument('--cpu-threads',type=int,default=4)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.phase=='compare':
        if not args.gaussian_output:
            raise ValueError('ZERO_MLP_COMPARISON_REQUIRES_GAUSSIAN_OUTPUT')
        result=compare_outputs(args.output,args.gaussian_output)
        print('ZERO_MLP_GAUSSIAN_COMPARISON='+json.dumps(result['summary'],sort_keys=True),flush=True)
        return result
    if not args.ladder_output:
        raise ValueError('ZERO_MLP_TRAIN_REQUIRES_LADDER_OUTPUT')
    return run(args)


if __name__=='__main__':
    main()
