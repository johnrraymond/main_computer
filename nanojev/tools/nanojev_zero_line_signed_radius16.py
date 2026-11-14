#!/usr/bin/env python3
"""Exactly learn EVERY signed cancellation residual -16..+16, at all operand scales.

Finetune the recurrent backbone of the accepted zero-only checkpoint; protect
original radius-127 arithmetic and the accepted scale-wide zero detector.
No arithmetic executor or answer-aware router is installed at inference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_ladder import atomic_checkpoint, atomic_json, deck, sample_deck, evaluate as legacy_evaluate
from tools.nanojev_zero_line_radius255_probe import load_certified_model, sha256
from tools.nanojev_zero_line_zero_only import (
    BANDS, MAX_ABS, SCHEMA as ZERO_SCHEMA, build_probe as previous_probe,
    classify as zero_classify, legacy_loss, input_tensor,
)
from tools import nanojev_addition_curriculum, nanojev_zero_line_ladder, nanojev_zero_line_zero_only

SCHEMA = 'nanojev.zero-line-signed-radius16.v1'
RESIDUALS = tuple(range(-16, 17))
RADIUS = 16
SPLITS = ('train', 'dev', 'test')


def magnitude_fold(m, seed=20261116):
    """Disjoint absolute-maximum-magnitude families, including sign/swaps."""
    if not 0 <= m <= MAX_ABS:
        raise ValueError('ZERO_R16_BAD_MAGNITUDE')
    if m == MAX_ABS:
        return 'test'
    digest = hashlib.sha256(f'r16-family:{seed}:{m}'.encode()).digest()[0] % 10
    return 'train' if digest < 6 else 'dev' if digest < 8 else 'test'


def band_of(m):
    for k, (lo, hi) in enumerate(BANDS):
        if lo <= m <= hi:
            return k
    raise ValueError('ZERO_R16_OUTSIDE_BANDS')


def pair_for_max(m, delta, swap=False, zero_direction=1):
    """Generate signed near-cancellation with actual max(abs(a),abs(b)) == m.

    Labels are generation-only metadata; the model receives the two operands.
    For nonzero delta, choose a with delta's sign and magnitude m. This also
    permits ALL residuals at the 2,000,000 endpoint without going out of range.
    """
    if abs(delta) > RADIUS or not 0 <= m <= MAX_ABS or m < abs(delta):
        return None
    s = (1 if delta > 0 else -1) if delta else (1 if zero_direction > 0 else -1)
    a, b = s * m, delta - s * m
    if not (max(abs(a), abs(b)) == m and abs(a) <= MAX_ABS and abs(b) <= MAX_ABS):
        raise AssertionError('ZERO_R16_PAIR_INVARIANT')
    return (b, a) if swap else (a, b)


def anchors(band, split, seed, count):
    """Fixed magnitudes independent of any model score; no full-range allocation."""
    lo, hi = BANDS[band]
    lo = max(lo, RADIUS)
    if count <= 0:
        raise ValueError('ZERO_R16_INVALID_ANCHOR_COUNT')
    candidates = [lo, lo + 1, hi - 1, hi]
    for power in (10, 100, 1000, 10000, 100000, 1000000, MAX_ABS):
        candidates.extend((power - 1, power, power + 1))
    rng = random.Random(f'r16-probe:{seed}:{band}:{split}')
    # Deterministic spread over the band, not just random local clusters.
    candidates += [lo + (hi - lo) * j // max(count - 1, 1) for j in range(count)]
    out, seen = [], set()
    for m in candidates:
        if lo <= m <= hi and m not in seen and magnitude_fold(m, seed) == split:
            seen.add(m)
            out.append(m)
            if len(out) == count:
                return sorted(out)
    for _ in range(count * 800):
        if len(out) == count:
            break
        m = rng.randint(lo, hi)
        if m not in seen and magnitude_fold(m, seed) == split:
            seen.add(m)
            out.append(m)
    if len(out) < count:
        # Low range may have fewer members in a partition than count. Fail
        # rather than silently pretending every intended cell was tested.
        raise RuntimeError(f'ZERO_R16_TOO_FEW_{split.upper()}_MAGNITUDES {band} {len(out)}/{count}')
    return sorted(out)


def build_matrix(split, seed, anchors_per_band):
    if split not in SPLITS:
        raise ValueError('ZERO_R16_BAD_SPLIT')
    rows = []
    for band in range(len(BANDS)):
        for m in anchors(band, split, seed, anchors_per_band):
            for delta in RESIDUALS:
                if delta:
                    pairs = (pair_for_max(m, delta, False), pair_for_max(m, delta, True))
                else:
                    pairs = tuple(pair_for_max(m, 0, swap, direction)
                                  for direction in (-1, 1) for swap in (False, True))
                for pair in pairs:
                    if pair is None:
                        continue
                    a, b = pair
                    rows.append((a, b, delta, band, m))
    # duplicate zero swaps across opposite directions can occur at m=0, but
    # lowest anchor is 16. Unique ordered pairs avoid overstating coverage.
    return list({(a, b): (a, b, d, band, m) for a, b, d, band, m in rows}.values())


def sample_family(rng, band, split, delta, seed):
    lo, hi = BANDS[band]
    lo = max(lo, RADIUS)
    for _ in range(6000):
        m = rng.randint(lo, hi)
        if magnitude_fold(m, seed) != split:
            continue
        direction = (-1 if rng.randrange(2) else 1)
        pair = pair_for_max(m, delta, bool(rng.randrange(2)), direction)
        if pair is not None:
            return (*pair, delta, band, m)
    raise RuntimeError(f'ZERO_R16_SAMPLING_EXHAUSTED band={band} split={split} d={delta}')


def train_cards(rng, batch_size, seed, weights, cursor):
    """Cover all 33 signed residuals and 13 magnitude bands on a fixed cycle.

    The first N-33 items form a joint (band, residual) lattice.  The last 33
    (if room) are TRAIN-only, difficulty-weighted band replay. Every residual
    appears equally often in a 132-card batch; no DEV/TEST scores drive mining.
    The absolute cursor is persisted to prevent skip/repeat after resume.
    """
    if batch_size < len(RESIDUALS):
        raise ValueError('ZERO_R16_BATCH_MUST_COVER_ALL_RESIDUALS')
    mined_count = len(RESIDUALS) if batch_size >= 2 * len(RESIDUALS) else 0
    uniform_count = batch_size - mined_count
    rows = []
    for i in range(batch_size):
        # Labels cycle independently of the magnitude, preventing inadvertent
        # special treatment of +/-16 or sign-skewed curricula.
        d = RESIDUALS[(cursor + i) % len(RESIDUALS)]
        if i < uniform_count:
            band = ((cursor + i) // len(RESIDUALS)) % len(BANDS)
        else:
            band = rng.choices(range(len(BANDS)), weights=weights, k=1)[0]
        rows.append(sample_family(rng, band, 'train', d, seed))
    rng.shuffle(rows)
    return rows, cursor + batch_size


def exact_supervision(out, deltas, zero_weight=2.0):
    """Canonical sign and every decimal digit supervised for EVERY residual."""
    targets = torch.tensor(deltas, dtype=torch.long, device=out['sign'].device)
    signs = F.cross_entropy(out['sign'], targets.sign() + 1, reduction='none')
    digits = F.cross_entropy(out['digits'].transpose(1, 2), target_digits(targets), reduction='none').mean(-1)
    weights = torch.where(targets == 0, torch.as_tensor(zero_weight, device=targets.device),
                          torch.ones_like(targets, dtype=torch.float))
    return ((signs + digits) * weights).sum() / weights.sum()


@torch.inference_mode()
def assess(model, rows, device, batch_size=256):
    """Full residual x magnitude matrix, canonical exact equality only."""
    model.eval()
    stats = {(k, d): [0, 0] for k in range(len(BANDS)) for d in RESIDUALS}
    examples = []
    for offset in range(0, len(rows), batch_size):
        block = rows[offset:offset+batch_size]
        out = model(input_tensor(block, device))
        pred_sign = out['sign'].argmax(-1) - 1
        pred_digits = out['digits'].argmax(-1)
        target = torch.tensor([r[2] for r in block], dtype=torch.long, device=device)
        valid = (pred_sign == target.sign()) & (pred_digits == target_digits(target)).all(-1)
        for (a,b,delta,band,m), ok in zip(block, valid.tolist()):
            stats[(band,delta)][0] += int(ok)
            stats[(band,delta)][1] += 1
            if not ok and len(examples) < 80:
                examples.append({'a':a,'b':b,'delta':delta,'band':list(BANDS[band]),'m':m})
    matrix = {}
    worst = 1.
    populated = True
    for k,(lo,hi) in enumerate(BANDS):
        cells = {}
        for d in RESIDUALS:
            good, total = stats[(k,d)]
            if not total:
                populated = False
            rate = good / total if total else None
            if rate is not None:
                worst = min(worst, rate)
            cells[str(d)] = {'correct':good,'total':total,'exact':rate}
        matrix[f'{lo}-{hi}'] = cells
    return {'cards':len(rows),'residuals':list(RESIDUALS),'bands':len(BANDS),
            'worst_cell_exact':worst if populated else None,
            'every_cell_populated':populated,
            'passes':bool(populated and worst == 1.),'matrix':matrix,'failures':examples}


def train_step(model, origin_teacher, optimizer, rng, args, weights, cursor):
    rows, cursor = train_cards(rng, args.batch_size, args.seed, weights, cursor)
    x = input_tensor(rows, args.device)
    legacy_x = torch.randint(-127,128,(args.legacy_batch_size,2),device='cpu').to(args.device)
    model.train()
    signed = exact_supervision(model(x), [r[2] for r in rows], args.zero_weight)
    historical = legacy_loss(model, origin_teacher, legacy_x)
    loss = signed + args.legacy_weight * historical
    if not bool(torch.isfinite(loss)):
        raise FloatingPointError('ZERO_R16_NONFINITE_LOSS')
    optimizer.zero_grad(set_to_none=True)
    loss.backward()
    nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.,error_if_nonfinite=True)
    optimizer.step()
    return float(loss.detach()), float(signed.detach()), float(historical.detach()), cursor


def source_model(ladder_output, zero_only_output, device):
    """Verify certified zero-only provenance back to the original radius-127 source."""
    model, origin = load_certified_model(ladder_output, device)
    root = Path(zero_only_output).resolve()
    cp, conf = root/'promoted.pt', root/'config.json'
    if not (cp.is_file() and conf.is_file()):
        raise FileNotFoundError('ZERO_R16_PROMOTED_ZERO_ONLY_SOURCE_MISSING')
    cfg = json.loads(conf.read_text('utf-8'))
    st = torch.load(cp, map_location='cpu', weights_only=True)
    if (cfg.get('schema') != ZERO_SCHEMA or st.get('schema') != ZERO_SCHEMA+'.promoted'
            or st.get('historical_exact') != 65025
            or st.get('source_checkpoint_sha256') != origin['checkpoint_sha256']
            or st.get('config_sha256') != sha256(conf)
            or cfg.get('certified_source',{}).get('checkpoint_sha256') != origin['checkpoint_sha256']
            or st.get('dev',{}).get('passes') is not True
            or st.get('heldout',{}).get('passes') is not True):
        raise RuntimeError('ZERO_R16_UNCERTIFIED_OR_PROVENANCE_MISMATCH')
    model.load_state_dict(st['model'], strict=True)
    model.eval()
    for p in model.parameters(): p.requires_grad_(False)
    return model, {'zero_promoted':str(cp),'zero_promoted_sha256':sha256(cp),
                   'zero_config_sha256':sha256(conf),'step':st['step'],
                   'original_certified':origin}


def configuration(args, provenance):
    keys = ('seed','batch_size','legacy_batch_size','legacy_weight','zero_weight',
            'lr','probe_every','eval_batch_size','legacy_probe_cards','anchors_per_band',
            'train_probe_anchors','stability_probes','parent_anchor_count','cpu_threads')
    return {'schema':SCHEMA,'code_sha256':sha256(__file__),
            'backbone_code_sha256':sha256(nanojev_addition_curriculum.__file__),
            'ladder_code_sha256':sha256(nanojev_zero_line_ladder.__file__),
            'parent_code_sha256':sha256(nanojev_zero_line_zero_only.__file__),
            'source':provenance,'settings':{k:getattr(args,k) for k in keys},
            'heldout_policy':'per-phase magnitude-family partition; one-time sealed TEST; no TEST-based training',
            'legacy_scope':'complete 65,025 certified pairs; previous zero-only sampled historical regression'}


def snapshot(model, optimizer, rng, step, streak, best, weights, cursor):
    return {'schema':SCHEMA+'.checkpoint','model':model.state_dict(),
            'optimizer':optimizer.state_dict(),'rng':rng.getstate(),
            'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            'step':step,'streak':streak,'best':best,'weights':weights,'cursor':cursor}


def restore(st, model, optimizer, rng):
    if st.get('schema') != SCHEMA+'.checkpoint':
        raise RuntimeError('ZERO_R16_BAD_RESUME_CHECKPOINT')
    model.load_state_dict(st['model'],strict=True)
    optimizer.load_state_dict(st['optimizer'])
    rng.setstate(st['rng'])
    torch.set_rng_state(st['torch_rng'])
    if torch.cuda.is_available() and st['cuda_rng'] is not None:
        torch.cuda.set_rng_state_all(st['cuda_rng'])
    return st['step'],st['streak'],st['best'],st['weights'],st['cursor']


def plan(args):
    return {'schema':SCHEMA, 'target_residuals':list(RESIDUALS),
            'target_count':len(RESIDUALS),'magnitude_bands':[list(b) for b in BANDS],
            'max_operand':MAX_ABS,'historical_full_deck':65025,
            'required_stable_dev_probes':args.stability_probes,
            'sampling':'per-band and per-signed-residual; exact canonical integer output',
            'promotion':'100% per populated cell, all 429 cells populated; retention + sealed test'}


def check_args(args):
    if (args.max_steps < 1 or args.probe_every < 1 or args.batch_size < len(RESIDUALS)
            or args.legacy_batch_size < 1 or args.eval_batch_size < 1 or
            args.anchors_per_band < 3 or args.train_probe_anchors < 1 or
            args.parent_anchor_count < 1 or args.legacy_probe_cards < 1 or
            args.stability_probes < 2 or args.cpu_threads < 1 or
            not (0. < args.lr < 1.) or not (0 < args.zero_weight < 100) or
            not (0 < args.legacy_weight < 100)):
        raise ValueError('ZERO_R16_INVALID_ARGUMENTS')
    if args.phase != 'plan' and args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('ZERO_R16_CUDA_UNAVAILABLE')


def run(args):
    check_args(args)
    if args.phase == 'plan':
        ans = plan(args)
        print('ZERO_R16_PLAN='+json.dumps(ans,sort_keys=True),flush=True)
        return ans
    torch.set_num_threads(args.cpu_threads)
    root = Path(args.output).resolve()
    parent, provenance = source_model(args.ladder_output,args.zero_only_output,args.device)
    source_hash = provenance['zero_promoted_sha256']
    config = configuration(args,provenance)
    conf_path = root/'config.json'
    if args.resume:
        if not conf_path.is_file() or json.loads(conf_path.read_text('utf-8')) != config:
            raise RuntimeError('ZERO_R16_CONFIG_OR_SOURCE_DRIFT')
        if (root/'test_opened.json').exists() or (root/'promoted.pt').exists():
            raise RuntimeError('ZERO_R16_SEALED_TEST_ALREADY_OPENED')
    else:
        if root.exists() and any(root.iterdir()):
            raise FileExistsError('ZERO_R16_OUTPUT_OVERWRITE_REFUSED')
        root.mkdir(parents=True,exist_ok=True)
        atomic_json(conf_path,config)
    model, certified = load_certified_model(args.ladder_output,args.device)
    model.load_state_dict(parent.state_dict(),strict=True)
    parent.eval()
    original, _ = load_certified_model(args.ladder_output,args.device)
    original.eval().requires_grad_(False)
    # Direct backbone finetuning. No new bank, inference selector, or arithmetic executor.
    for name, p in model.named_parameters():
        p.requires_grad_(name.startswith('backbone.') or name.startswith('norm.') or name in ('query','pos'))
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),
                                  lr=args.lr,weight_decay=0.)
    rng = random.Random(args.seed + 367)
    torch.manual_seed(args.seed + 181)
    step, streak, best, cursor = 0,0,-1.,0
    weights = [1.]*len(BANDS)
    if args.resume:
        if not (root/'latest.pt').is_file():
            raise FileNotFoundError('ZERO_R16_RESUME_MISSING')
        step, streak, best, weights, cursor = restore(
            torch.load(root/'latest.pt',map_location='cpu',weights_only=True),model,optimizer,rng)
    # All DEV/TEST examples fixed independently of the model and training.
    train_probe = build_matrix('train',args.seed,args.train_probe_anchors)
    dev = build_matrix('dev',args.seed,args.anchors_per_band)
    test = build_matrix('test',args.seed,args.anchors_per_band)
    if any(not rows for rows in (train_probe,dev,test)):
        raise RuntimeError('ZERO_R16_EMPTY_SPLIT')
    legacy_rows = deck(127)
    legacy_sample = sample_deck(legacy_rows,args.legacy_probe_cards,args.seed+34)
    # Previous campaign's DEV+TEST become HISTORICAL audit sets, not new test.
    previous_seed = int(json.loads((Path(args.zero_only_output)/'config.json').read_text('utf-8'))['settings']['seed'])
    retained = {}
    for s in ('dev','test'):
        raw = previous_probe(s,previous_seed,args.parent_anchor_count)
        retained[s] = raw
    base_path = root/'baseline.json'
    if not args.resume:
        base = assess(parent,dev,args.device,args.eval_batch_size)
        zero_old = {s:zero_classify(parent,rows,args.device,args.eval_batch_size)
                    for s,rows in retained.items()}
        atomic_json(base_path,{'signed':base,'parent_zero_audits':zero_old})
        print('ZERO_R16_BASELINE='+json.dumps({
            'cards':base['cards'],'worst_signed_cell':base['worst_cell_exact'],
            'previous_zero_dev':zero_old['dev']['min_zero'],
            'previous_zero_test':zero_old['test']['min_zero']},sort_keys=True),flush=True)
    else:
        if not base_path.is_file():
            raise RuntimeError('ZERO_R16_MISSING_BASELINE')
    report = {'status':'training','source':provenance,'last_step':step,'best_dev_min':best}
    for step in range(step+1,args.max_steps+1):
        loss,signed_loss,old_loss,cursor = train_step(model,original,optimizer,rng,args,weights,cursor)
        if step % args.probe_every and step != args.max_steps:
            continue
        train_diag = assess(model,train_probe,args.device,args.eval_batch_size)
        weights = [1. + 4.*sum(1.-(train_diag['matrix'][f'{lo}-{hi}'][str(d)]['exact'] or 0.)
                                for d in RESIDUALS)/len(RESIDUALS)
                   for lo,hi in BANDS]
        dev_result = assess(model,dev,args.device,args.eval_batch_size)
        old_probe = legacy_evaluate(model,legacy_sample,args.device,args.eval_batch_size)
        zero_history = {s:zero_classify(model,rows,args.device,args.eval_batch_size)
                        for s,rows in retained.items()}
        worst = dev_result['worst_cell_exact']
        historical_probe_pass = old_probe['groups']['all']['passed'] == len(legacy_sample['y'])
        zero_history_pass = all(p['passes'] for p in zero_history.values())
        stable_pass = dev_result['passes'] and historical_probe_pass and zero_history_pass
        if worst is not None and worst > best:
            best = worst
            atomic_checkpoint(root/'best_dev.pt',{'schema':SCHEMA+'.best',
                'model':model.state_dict(),'step':step,'worst_cell':worst,
                'source_sha256':source_hash})
        streak = streak+1 if stable_pass else 0
        report.update({'status':'training','last_step':step,'loss':loss,
                       'signed_loss':signed_loss,'legacy_loss':old_loss,
                       'train_probe':train_diag,'dev':dev_result,
                       'previous_zero_retention':zero_history,
                       'history_sample':old_probe,'best_dev_min':best,
                       'streak':streak,'stability_target':args.stability_probes})
        atomic_checkpoint(root/'latest.pt',snapshot(model,optimizer,rng,step,streak,best,weights,cursor))
        atomic_json(root/'result.json',report)
        print('ZERO_R16_PROBE='+json.dumps({'step':step,'loss':round(loss,5),
              'dev_worst_signed_cell':worst,'zero_history_retained':zero_history_pass,
              'historical_probe_exact':old_probe['groups']['all']['accuracy'],
              'worst_train_band':max(range(len(weights)),key=lambda k:weights[k]),
              'streak':streak,'target':args.stability_probes},sort_keys=True),flush=True)
        if streak < args.stability_probes:
            continue
        full = legacy_evaluate(model,legacy_rows,args.device,args.eval_batch_size)
        report['historical_full'] = full
        if (full['groups']['all']['passed'] != 65025 or full['false_zero'] != 0):
            streak = 0
            report['status'] = 'retention_refused'
            report['reason'] = 'historical_65025_regression'
            atomic_checkpoint(root/'latest.pt',snapshot(model,optimizer,rng,step,streak,best,weights,cursor))
            atomic_json(root/'result.json',report)
            print('ZERO_R16_RETENTION_REFUSED='+json.dumps({'step':step,
                 'historical_passed':full['groups']['all']['passed'],
                 'historical_required':65025},sort_keys=True),flush=True)
            continue
        # Fail-closed one-time TEST seal, persisted BEFORE evaluating the TEST.
        atomic_json(root/'test_opened.json', {'step':step,'source_sha256':source_hash,
                    'status':'open_once_do_not_resume'})
        holdout = assess(model,test,args.device,args.eval_batch_size)
        report['holdout_once'] = holdout
        if holdout['passes']:
            report['status']='promoted'
            atomic_checkpoint(root/'promoted.pt',{
                'schema':SCHEMA+'.promoted','model':model.state_dict(),
                'step':step,'source_zero_promoted_sha256':source_hash,
                'config_sha256':sha256(conf_path),'historical_exact':65025,
                'dev':dev_result,'sealed_test':holdout,
                'parent_zero_retention':zero_history})
        else:
            report['status']='heldout_refused'
            report['reason']='residual_or_magnitude_instability'
        atomic_json(root/'result.json',report)
        print('ZERO_R16_CERTIFICATION='+json.dumps({'status':report['status'],
             'step':step,'worst_test_cell':holdout['worst_cell_exact'],
             'historical_exact':65025},sort_keys=True),flush=True)
        return report
    report['status']='budget_exhausted_no_promotion'
    atomic_json(root/'result.json',report)
    print('ZERO_R16_SUMMARY='+json.dumps({'status':report['status'],
          'step':args.max_steps,'best_dev_min':best,'promoted':False},sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('plan','train'),default='train')
    p.add_argument('--ladder-output',default='')
    p.add_argument('--zero-only-output',default='')
    p.add_argument('--output',default='')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--max-steps',type=int,default=30000)
    p.add_argument('--probe-every',type=int,default=250)
    p.add_argument('--batch-size',type=int,default=132)
    p.add_argument('--legacy-batch-size',type=int,default=128)
    p.add_argument('--legacy-weight',type=float,default=2.)
    p.add_argument('--zero-weight',type=float,default=3.)
    p.add_argument('--lr',type=float,default=.00005)
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--legacy-probe-cards',type=int,default=2048)
    p.add_argument('--anchors-per-band',type=int,default=8)
    p.add_argument('--train-probe-anchors',type=int,default=4)
    p.add_argument('--parent-anchor-count',type=int,default=20)
    p.add_argument('--stability-probes',type=int,default=3)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--seed',type=int,default=20261116)
    p.add_argument('--resume',action='store_true')
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':
    main()
