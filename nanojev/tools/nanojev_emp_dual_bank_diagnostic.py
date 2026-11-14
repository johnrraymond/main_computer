#!/usr/bin/env python3
"""Read-only, case-level diagnostic for a stalled four-bank outward ladder stage.

Uses only the campaign's frozen DEV generators; never opens sealed TEST, never
calls optimizer.step(), never rewrites the campaign, and never changes a gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path

import torch

from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_dual_bank_error_monitor as errmon
from tools import nanojev_emp_hard_case_detector as detector
from tools import nanojev_emp_carry_intervention as carry_intervention
from tools import nanojev_zero_line_outward_return as outward
from tools import nanojev_zero_line_sampled_radius_ladder as old
from tools.nanojev_zero_line_radius255_probe import sha256
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import atomic_json

SCHEMA = 'nanojev.emp-dual-bank-diagnostic.v1'
HARD_SCHEMA = 'nanojev.emp-dual-bank-hard-train-cases.v1'
HARD_SPLIT_ALGORITHM = 'specialist-family-reserve-sha256-v1'



def correct(target, prediction):
    if target == 0:
        return prediction == 0
    return abs(prediction / target - 1.0) <= .05 + 1e-12


def error_type(target, prediction):
    if target == 0:
        return 'exact_zero' if prediction == 0 else 'false_nonzero'
    q = prediction / target
    if q <= 0:
        return 'tipping'
    if q < .95 - 1e-12:
        return 'toward_zero'
    if q > 1.05 + 1e-12:
        return 'overshoot'
    return 'within_5pct'


def shell(value):
    n = abs(int(value))
    if not n:
        return 'zero'
    lower = 1 << (n.bit_length() - 1)
    return f'{lower}..{lower * 2 - 1}'


def group_label(row, key):
    a, b, d, band, m = row
    if key == 'residual_shell':
        return shell(d)
    if key == 'operand_shell':
        return shell(max(abs(a), abs(b)))
    if key == 'residual_sign':
        return 'positive' if d > 0 else ('negative' if d < 0 else 'zero')
    if key == 'digits':
        return str(len(str(abs(d)))) if d else 'zero'
    if key == 'input_signs':
        return 'opposite' if a * b < 0 else ('zero_operand' if not a or not b else 'same')
    if key == 'anchor_band':
        return str(band)
    raise ValueError(key)


def summarize_rows(rows, predicted):
    if not rows:
        return {'n': 0}
    stats = errmon.error_statistics(rows, predicted)
    kinds = {name: 0 for name in ('within_5pct', 'toward_zero', 'overshoot', 'tipping', 'exact_zero', 'false_nonzero')}
    for row, pred in zip(rows, predicted):
        kinds[error_type(int(row[2]), int(pred))] += 1
    return {
        'n': len(rows), 'within_5pct': sum(correct(int(row[2]), int(pred)) for row, pred in zip(rows, predicted)) / len(rows),
        'error_types': kinds,
        'raw_error_bias': stats['signed_error']['mean'],
        'raw_error_stddev': stats['signed_error']['stddev'],
        'scale_bias': stats['signed_scale_error']['mean'],
        'scale_stddev': stats['signed_scale_error']['stddev'],
        'scale_median': statistics.median([int(pred) / int(row[2]) - 1 for row,pred in zip(rows,predicted) if int(row[2])]) if stats['nonzero'] else None,
        'mean_abs_error': stats['absolute_error_mean'],
        'error_percentiles': high_resolution_error(rows, predicted),
    }


def analyze_deck(rows, predictions, top=12):
    """One fixed DEV deck, identical cards in identical order for all four banks."""
    if set(predictions) != set(dual.BANKS) or any(len(p) != len(rows) for p in predictions.values()):
        raise ValueError('EMP_DIAG_BANK_ALIGNMENT_MISMATCH')
    summary = {bank: summarize_rows(rows, predictions[bank]) for bank in dual.BANKS}
    left, right = predictions['left.left'], predictions['right.right']
    def compare(left_bank,right_bank):
        counters = {'both_correct': 0, 'left_only': 0, 'right_only': 0, 'both_wrong': 0,
                    'right_lower_absolute_error': 0, 'left_lower_absolute_error': 0, 'tied_absolute_error': 0}
        for card,l,r in zip(rows,predictions[left_bank],predictions[right_bank]):
            target=int(card[2]); lc=correct(target,int(l));rc=correct(target,int(r))
            counters[('both_correct' if lc else 'right_only') if rc else ('left_only' if lc else 'both_wrong')] += 1
            if abs(r-target)<abs(l-target):counters['right_lower_absolute_error']+=1
            elif abs(l-target)<abs(r-target):counters['left_lower_absolute_error']+=1
            else:counters['tied_absolute_error']+=1
        return counters
    pairing=compare('left.left','right.right')
    within_emp={emp:compare(f'{emp}.left',f'{emp}.right') for emp in dual.EMPS}
    dimensions = {}
    for axis in ('residual_shell','operand_shell','residual_sign','digits','input_signs','anchor_band'):
        indices = defaultdict(list)
        for i,row in enumerate(rows):
            indices[group_label(row,axis)].append(i)
        dimensions[axis] = {
            key: {bank: summarize_rows([rows[i] for i in ids], [predictions[bank][i] for i in ids])
                  for bank in dual.BANKS} for key,ids in sorted(indices.items())
        }
    failures=[]
    for card,l,r,other_l,other_r in zip(rows,left,right,predictions['left.right'],predictions['right.left']):
        a,b,d,band,m = card
        if correct(d,l) or not correct(d,r):
            continue
        failures.append({
            'a':int(a),'b':int(b),'target':int(d),'anchor':int(m),'anchor_band':int(band),
            'left_left':int(l),'left_right':int(other_l),'right_left':int(other_r),'right_right':int(r),
            'left_error':int(l-d),'right_error':int(r-d),
            'left_ratio': l/d if d else None,
            'left_failure':error_type(d,l),'residual_shell':shell(d),
        })
    failures.sort(key=lambda r: (abs(r['left_ratio']-1) if r['left_ratio'] is not None else abs(r['left_error'])),reverse=True)
    return {'cards':len(rows),'summary':summary,'pair_left_left_vs_right_right':pairing,
            'within_emp_pairs':within_emp,'groups':dimensions,'right_correct_left_wrong_examples':failures[:top],
            'right_correct_left_wrong_total':len(failures)}


def optimizer_diagnostics(checkpoint):
    controls = dual.checked_controls(checkpoint['controls'])
    result = {}
    for bank in dual.BANKS:
        item = checkpoint.get('optimizers',{}).get(bank,{})
        groups=item.get('param_groups',[])
        counts=[]
        for state in item.get('state',{}).values():
            s=state.get('step')
            if s is not None:
                counts.append(int(s.item()) if isinstance(s,torch.Tensor) else int(s))
        result[bank]={
            'settings':controls['banks'][bank],
            'optimizer_parameter_step_count':len(counts),
            'optimizer_step_min':min(counts) if counts else None,
            'optimizer_step_max':max(counts) if counts else None,
            'optimizer_step_median':statistics.median(counts) if counts else None,
            'optimizer_group_lrs':[{ 'section':g.get('section'), 'lr':g['lr']} for g in groups],
        }
    return {'revision':controls['revision'],'banks':result}


def _safe_checkpoint(path,radius):
    if not path.is_file():
        raise FileNotFoundError(f'EMP_DIAG_CHECKPOINT_MISSING:{path}')
    before=sha256(path)
    state=torch.load(path,map_location='cpu',weights_only=True)
    if before!=sha256(path):
        raise RuntimeError('EMP_DIAG_CHECKPOINT_CHANGED_DURING_READ')
    if state.get('schema')!=ladder.SCHEMA+'.checkpoint' or state.get('radius')!=radius:
        raise RuntimeError('EMP_DIAG_CHECKPOINT_SCHEMA_MISMATCH')
    if set(state['optimizers'])!=set(dual.BANKS) or 'banks' not in state:
        raise RuntimeError('EMP_DIAG_FOUR_BANK_STATE_INCOMPLETE')
    return state,before


def _build_models(baseline,left,right,checkpoint,device):
    return ladder.from_source(baseline,left,right,checkpoint['banks'],device)


def dev_cards(cfg,radius):
    seed=cfg['seed']
    return {
        'frontier':[outward.frontier_cards(radius,'dev',seed,v,cfg['dev_cards']) for v in range(2)],
        'breadth':old.evaluation_cards(radius,'dev',seed,radius,
                    cfg['breadth_anchors'],2,cfg['breadth_outer_per_anchor'])
    }


def predict(banks, decks, device, batch_size):
    for _,bank in banks.items():bank.eval()
    output={}
    with torch.inference_mode():
        for kind, views in decks.items():
            for view,rows in enumerate(views):
                vals={name:[] for name in dual.BANKS}
                for start in range(0,len(rows),batch_size):
                    x=input_tensor(rows[start:start+batch_size],device)
                    for name,model in banks.items():
                        vals[name].extend(dual.decoded(model(x)))
                if kind=='frontier':
                    selected=list(range(len(rows)))
                    key='frontier'
                else:
                    selected=[i for i,row in enumerate(rows) if abs(row[2])>32]
                    key='outer'
                output[f'{key}.dev_{view}']=( [rows[i] for i in selected],
                    {name:[vals[name][i] for i in selected] for name in dual.BANKS})
                if kind=='breadth':
                    for subset,condition in (
                        ('inner',lambda d:0<abs(d)<=32),('zero',lambda d:d==0)):
                        chosen=[i for i,row in enumerate(rows) if condition(row[2])]
                        output[f'{subset}.dev_{view}']=( [rows[i] for i in chosen],
                            {name:[vals[name][i] for i in chosen] for name in dual.BANKS})
    return output



def specialist_partition(anchor, seed):
    """Additional *specialist-only* anchor split within the established TRAIN fold.

    One in five train-fold anchor families is reserved and NEVER examined by the
    diagnostic hard-case miner. The eventual specialist experiment must apply
    this same partition when constructing new, unseen examples. This does not
    make the original four-bank pretraining independently held out.
    """
    digest = hashlib.sha256(f'emp-specialist-reserved:v1:{seed}:{int(anchor)}'.encode()).digest()
    return 'reserve' if int.from_bytes(digest[:8], 'big') % 5 == 0 else 'mine'


def mine_train_cards(radius, seed, count, batch_size=256, *,
                     namespace="emp-hard-train-candidates:v1", excluded_anchors=()):
    """Deterministic, bounded TRAIN-only pool, disjoint from DEV and sealed TEST.

    Uses precisely the outward ladder's legal training generator; labels are
    generated from TRAIN operand pairs and never derived from predictions.
    This is a fresh diagnostic pool, not a replay log of actual past batches.
    """
    if count < 1 or batch_size < 16:
        raise ValueError('EMP_DIAG_BAD_HARD_CANDIDATES')
    rng = random.Random(f'{namespace}:{seed}:{radius}')
    excluded_anchors = set(excluded_anchors)
    cursor = 0
    weights = [1.0] * len(old.BANDS)
    kept = []
    seen = set()
    attempted = 0
    reserved = 0
    # The bound prevents a pathological split/sampler from running indefinitely.
    for _ in range(30 * (count // batch_size + 1)):
        if len(kept) >= count:
            break
        batch, cursor = outward.generate_batch(
            rng, radius, seed, max(16, min(batch_size, count)), weights,
            cursor, 'outward', radius, radius)
        for card in batch:
            a, b, target, band, m = (int(v) for v in card)
            attempted += 1
            if old.stable_fold(m, seed) != 'train':
                raise RuntimeError('EMP_DIAG_TRAIN_SPLIT_LEAK')
            if specialist_partition(m, seed) == 'reserve':
                reserved += 1
                continue
            if m in excluded_anchors:
                continue
            if (a, b) in seen:
                continue
            seen.add((a, b))
            kept.append((a, b, target, band, m))
            if len(kept) == count:
                break
    if len(kept) < count:
        raise RuntimeError(f'EMP_DIAG_HARD_POOL_INCOMPLETE:{len(kept)}/{count}')
    return kept, {'requested': count, 'attempted': attempted,
                  'reserved_skipped': reserved, 'eligible_unique': len(kept)}


def predict_training_cases(banks, rows, device, batch_size):
    """Predict over a fixed TRAIN pool, never touching DEV/TEST or optimizer."""
    result = {bank: [] for bank in dual.BANKS}
    for bank in banks.values():
        bank.eval()
    with torch.inference_mode():
        for start in range(0, len(rows), batch_size):
            x = input_tensor(rows[start:start + batch_size], device)
            for name, model in banks.items():
                result[name].extend(int(p) for p in dual.decoded(model(x)))
    if any(len(values) != len(rows) for values in result.values()):
        raise RuntimeError('EMP_DIAG_HARD_PREDICTION_ALIGNMENT')
    return result


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def high_resolution_error(rows, predictions):
    """Robust error percentiles complement the existing SD and mean bias."""
    if len(rows) != len(predictions):
        raise ValueError('EMP_DIAG_HARD_ALIGNMENT')
    nonzero = [(int(row[2]), int(pred)) for row, pred in zip(rows, predictions) if int(row[2])]
    ae = [abs(pred - target) for target, pred in nonzero]
    are = [abs(pred - target) / abs(target) for target, pred in nonzero]
    return {'nonzero': len(nonzero),
            'absolute_error': {'median': _quantile(ae, .5), 'p90': _quantile(ae, .9), 'p99': _quantile(ae, .99)},
            'absolute_relative_error': {'median': _quantile(are, .5), 'p90': _quantile(are, .9), 'p99': _quantile(are, .99)},
            'outside_5pct': sum(x > .05 + 1e-12 for x in are),
            'gt_100pct': sum(x > 1.0 for x in are)}


def rank_hard_cases(rows, predicted, chosen_pair, max_cases, max_per_anchor=8, min_failures=2):
    """Failure-led selection across all four, independent of EMP ownership.

    If either selected role-bank gets a case wrong it is observable, but hard
    export defaults to at least two failing banks. Near-zero denominators do
    not overwhelm the ordering: relative severity is capped at 10.
    """
    if len(set(chosen_pair)) != 2 or any(b not in dual.BANKS for b in chosen_pair):
        raise ValueError('EMP_DIAG_BAD_SELECTED_PAIR')
    if max_cases < 1 or max_per_anchor < 1 or not 1 <= min_failures <= len(dual.BANKS):
        raise ValueError('EMP_DIAG_BAD_HARD_SELECTION')
    if set(predicted) != set(dual.BANKS) or any(len(p) != len(rows) for p in predicted.values()):
        raise ValueError('EMP_DIAG_HARD_ALIGNMENT')
    pool = []
    breakdown = {'all_four_wrong': 0, 'both_selected_wrong': 0,
                 'three_or_more_wrong': 0, 'two_or_more_wrong': 0,
                 'any_wrong': 0, 'all_correct': 0}
    by_shell = defaultdict(lambda: {'total': 0, 'hard': 0, 'shared_four': 0})
    by_cohort = {axis: defaultdict(lambda: {'total': 0, 'hard': 0, 'all_four_wrong': 0,
                                            'pair_wrong': 0, 'per_bank_failures': {bank: 0 for bank in dual.BANKS}})
                 for axis in ('residual_shell', 'operand_shell', 'residual_sign', 'input_signs', 'digits', 'anchor_band')}
    for i, card in enumerate(rows):
        a, b, d, band, m = (int(v) for v in card)
        failures = [bank for bank in dual.BANKS if not correct(d, int(predicted[bank][i]))]
        n_wrong = len(failures)
        pair_wrong = all(b in failures for b in chosen_pair)
        shared_four = n_wrong == len(dual.BANKS)
        s = by_shell[shell(d)]
        s['total'] += 1
        s['hard'] += int(n_wrong >= min_failures)
        s['shared_four'] += int(shared_four)
        for axis, groups in by_cohort.items():
            key = group_label(card, axis)
            cohort = groups[key]
            cohort['total'] += 1
            cohort['hard'] += int(n_wrong >= min_failures)
            cohort['all_four_wrong'] += int(shared_four)
            cohort['pair_wrong'] += int(pair_wrong)
            for bank in failures:
                cohort['per_bank_failures'][bank] += 1
        breakdown['all_correct' if not n_wrong else 'any_wrong'] += 1
        breakdown['all_four_wrong'] += int(shared_four)
        breakdown['both_selected_wrong'] += int(pair_wrong)
        breakdown['three_or_more_wrong'] += int(n_wrong >= 3)
        breakdown['two_or_more_wrong'] += int(n_wrong >= 2)
        if n_wrong < min_failures:
            continue
        details = {bank: {'prediction': int(predicted[bank][i]),
                    'error': int(predicted[bank][i]) - d,
                    'failure_type': error_type(d, int(predicted[bank][i])),
                    'relative_error': (int(predicted[bank][i]) - d) / d if d else None}
                   for bank in dual.BANKS}
        severity = max(min(10., abs(int(predicted[bank][i]) - d) / max(1, abs(d))) for bank in failures)
        flips = sum(details[bank]['failure_type'] in ('tipping','false_nonzero') for bank in failures)
        row = {'a': a, 'b': b, 'target': d, 'anchor': m, 'anchor_band': band,
               'residual_shell': shell(d), 'residual_sign': 'positive' if d > 0 else 'negative' if d < 0 else 'zero',
               'input_signs': group_label(card, 'input_signs'),
               'wrong_banks': failures, 'wrong_count': n_wrong,
               'both_selected_wrong': pair_wrong, 'all_four_wrong': shared_four,
               'sign_failure_banks': flips, 'severity_capped': round(severity, 6),
               'predictions': details, 'specialist_partition': 'mine',
               'split': 'train', 'source': 'reproducible_train_sampler'}
        rank = (int(shared_four), int(pair_wrong), n_wrong, flips, severity, -i)
        pool.append((rank, row))
    pool.sort(key=lambda item: item[0], reverse=True)
    selected = []
    seen_anchor = defaultdict(int)
    for _, row in pool:
        if seen_anchor[row['anchor']] >= max_per_anchor:
            continue
        selected.append(row)
        seen_anchor[row['anchor']] += 1
        if len(selected) == max_cases:
            break
    return selected, {'scanned': len(rows), 'eligible_hard': len(pool),
                      'exported': len(selected), 'breakdown': breakdown,
                      'by_residual_shell': dict(sorted(by_shell.items())),
                      'cohorts': {axis: dict(sorted(groups.items())) for axis, groups in by_cohort.items()},
                      'selected_pair': list(chosen_pair),
                      'min_failing_banks': min_failures,
                      'max_per_anchor': max_per_anchor}


def _atomic_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def mine_hard_cases(banks, cfg, radius, args, checkpoint_hash, selected_pair, path):
    cards, pool_info = mine_train_cards(radius, cfg['seed'], args.hard_candidates)
    pred = predict_training_cases(banks, cards, args.device, args.eval_batch_size)
    chosen, summary = rank_hard_cases(cards, pred, selected_pair, args.hard_max_cases,
                                      args.hard_per_anchor, args.hard_min_failures)
    high_res = {bank: high_resolution_error(cards, pred[bank]) for bank in dual.BANKS}
    output = [dict(item, case_schema=HARD_SCHEMA, radius=radius,
                   checkpoint_sha256=checkpoint_hash) for item in chosen]
    detection = (detector.make_detector(cards, pred, selected_pair, cfg['seed'],
                 target=getattr(args, 'detector_target', 'both_selected_wrong'),
                 depth=getattr(args, 'detector_depth', 3),
                 min_leaf=getattr(args, 'detector_min_leaf', 48))
                 if getattr(args, 'detect_hard', False) else None)
    return {'schema': HARD_SCHEMA, 'source_split': 'train',
            'detector': detection,
            'heldout_policy': 'Within TRAIN, SHA-reserved anchor families were not evaluated or exported. '
                              'These are specialist-only reserved families, not independent base-model TEST.',
            'partition_algorithm': HARD_SPLIT_ALGORITHM, 'anchor_seed': cfg['seed'],
            'radius': radius, 'checkpoint_sha256': checkpoint_hash,
            'train_pool': pool_info, 'summary': summary,
            'error_percentiles': high_res,
            'export_path': str(path), 'export_sha256': None,
            'sample': output[:min(args.top, 5)]}, output


def run(args):
    root=Path(args.output).expanduser().resolve()
    cfg_path=root/'config.json'
    cfg=json.loads(cfg_path.read_text('utf-8'))
    if cfg.get('schema')!=ladder.SCHEMA:
        raise RuntimeError('EMP_DIAG_CAMPAIGN_SCHEMA_MISMATCH')
    stages=root/'stages'
    available=sorted(int(d.name[len('outward_'):]) for d in stages.glob('outward_*')
                     if d.is_dir() and (d/'latest.pt').is_file())
    if not available:
        raise FileNotFoundError('EMP_DIAG_NO_STAGE_CHECKPOINT')
    radius=args.radius if args.radius else available[-1]
    if radius not in available:
        raise FileNotFoundError('EMP_DIAG_RADIUS_MISSING')
    stage=stages/f'outward_{radius}'
    sc=json.loads((stage/'config.json').read_text('utf-8'))
    if sc.get('radius')!=radius or sc.get('campaign_config_sha256')!=sha256(cfg_path):
        raise RuntimeError('EMP_DIAG_STAGE_PROVENANCE_DRIFT')
    state,checkpoint_hash=_safe_checkpoint(stage/'latest.pt',radius)
    for name in ('source_left.pt','source_right.pt'):
        if sha256(root/name)!=cfg[('left_source_sha256' if name == 'source_left.pt' else 'right_source_sha256')]:
            raise RuntimeError('EMP_DIAG_SOURCE_SNAPSHOT_DRIFT')
    sources=[dual.checked_source(root/name) for name in ('source_left.pt','source_right.pt')]
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('EMP_DIAG_CUDA_UNAVAILABLE')
    torch.set_num_threads(args.cpu_threads)
    baseline,_=ladder.load_certified_model(args.ladder_output,'cpu')
    def inspect(model_state,rad,examples):
        banks=_build_models(baseline,*sources,model_state,args.device)
        try:
            raw=predict(banks,dev_cards(cfg,rad),args.device,args.eval_batch_size)
            return {label:analyze_deck(rows,preds,examples) for label,(rows,preds) in raw.items()}
        finally:
            del banks
    observed=inspect(state,radius,args.top)
    report={'schema':SCHEMA,'split':'dev','radius':radius,'step':state['step'],
            'checkpoint_sha256':checkpoint_hash,'campaign_config_sha256':sha256(cfg_path),
            'checkpoint_path':str(stage/'latest.pt'),
            'controls_and_optimizer':optimizer_diagnostics(state),
            'current':observed,'parent_comparison':None,
            'note':'Frozen DEV only, per-deck descriptive statistics; no TEST data, updates or gate changes.'}
    if args.compare_parent:
        older=sorted((r for r in available if r < radius and (stages/f'outward_{r}'/'accepted.pt').is_file()))
        if older:
            prior=older[-1]
            accepted=stages/f'outward_{prior}'/'accepted.pt'
            acc=json.loads((accepted.parent/'acceptance.json').read_text('utf-8'))
            if acc.get('checkpoint_sha256')!=sha256(accepted):
                raise RuntimeError('EMP_DIAG_PARENT_ACCEPTANCE_DRIFT')
            parent,parent_hash=_safe_checkpoint(accepted,prior)
            parent_on_current=inspect(parent,radius,0)
            current_on_old=inspect(state,prior,0)
            parent_on_old=inspect(parent,prior,0)
            report['parent_comparison']={
                'accepted_radius':prior,'accepted_sha256':parent_hash,
                'parent_on_current_radius':{k:v['summary'] for k,v in parent_on_current.items()},
                'current_on_parent_radius':{k:v['summary'] for k,v in current_on_old.items()},
                'parent_on_parent_radius':{k:v['summary'] for k,v in parent_on_old.items()},
            }
    # The two provisional winners have no fixed EMP ownership restriction.
    # Selection is frozen for this diagnosis and is not a routing decision.
    def min_score(bank):
        return min(observed[f'{deck}.dev_{view}']['summary'][bank]['within_5pct']
                   for deck in ('frontier', 'outer') for view in range(2))
    ordered = sorted(dual.BANKS, key=lambda bank: (-min_score(bank), dual.BANKS.index(bank)))
    chosen_pair = (ordered[0], ordered[1])
    hard_report = None
    hard_export_rows = None
    hard_dest = None
    if getattr(args, 'mine_hard', False) or getattr(args, 'detect_hard', False):
        hard_dest = (Path(args.hard_export).expanduser().resolve() if args.hard_export else
                     root.parent / (root.name + f'_hard_train_{radius}.jsonl'))
        if hard_dest == root or root in hard_dest.parents:
            raise ValueError('EMP_DIAG_HARD_EXPORT_MUST_BE_OUTSIDE_CAMPAIGN')
        if hard_dest.suffix.lower() != '.jsonl':
            raise ValueError('EMP_DIAG_HARD_EXPORT_MUST_BE_JSONL')
        destination = (Path(args.report).expanduser().resolve() if args.report else
                       root.parent / (root.name + f'_diagnostic_{radius}.json'))
        if hard_dest == destination:
            raise ValueError('EMP_DIAG_HARD_EXPORT_CONFLICTS_WITH_REPORT')
        # Failure labels come from TRAIN cases only; the pair is provisional.
        mine_banks = _build_models(baseline, *sources, state, args.device)
        try:
            hard_report, hard_export_rows = mine_hard_cases(mine_banks, cfg, radius, args,
                                                              checkpoint_hash, chosen_pair, hard_dest)
        finally:
            del mine_banks
        report['hard_mining'] = hard_report
    matched_report = None
    if getattr(args, 'matched_carry', False):
        # The discovery scan is a nuisance for causal hypothesis testing:
        # never re-use its anchors for this fresh, pre-specified contrast.
        discovery_cards, _ = mine_train_cards(radius, cfg['seed'], args.hard_candidates)
        discovery_anchors = {int(r[4]) for r in discovery_cards}
        new_cards, matched_sampling = mine_train_cards(
            radius, cfg['seed'], args.matched_candidates,
            namespace='emp-matched-carry-candidates:v1',
            excluded_anchors=discovery_anchors)
        matched_pairs, match_stats = carry_intervention.make_pairs(
            new_cards, args.matched_pairs, args.matched_max_target_drift)
        match_models = _build_models(baseline, *sources, state, args.device)
        try:
            matched_report = carry_intervention.analyze(
                matched_pairs,
                lambda cards: predict_training_cases(match_models, cards, args.device, args.eval_batch_size),
                checkpoint_hash, radius, state['step'], chosen_pair)
        finally:
            del match_models
        matched_report['sampling'] = {
            'original_detector_scan_anchors_excluded': len(discovery_anchors),
            'new_train_pool': matched_sampling, 'matching': match_stats,
            'independent_sampling_namespace': 'emp-matched-carry-candidates:v1',
            'train_family_reserve_opened': 0, 'dev_or_test_opened': 0}
        matched_report['interpretation'] = (
            'Paired per-sign comparisons, not raw cross-sign rates. Effect is'
            ' associated with changing lower digits to induce carry or borrow,'
            ' and may include suffix and answer changes. Do not certify causation.')
        report['matched_carry_intervention'] = matched_report
    if sha256(stage/'latest.pt')!=checkpoint_hash:
        raise RuntimeError('EMP_DIAG_CHECKPOINT_CHANGED_DURING_PROBE_STOP_TRAINER_FIRST')
    if hard_report:
        _atomic_jsonl(hard_dest, hard_export_rows)
        hard_report['export_sha256'] = sha256(hard_dest)
        # Record the immutable source checkpoint hash for every exported case.
        # A racing writer is still refused before the main diagnostic report.
        if sha256(stage/'latest.pt')!=checkpoint_hash:
            raise RuntimeError('EMP_DIAG_CHECKPOINT_CHANGED_DURING_PROBE_STOP_TRAINER_FIRST')
    dest=Path(args.report).expanduser().resolve() if args.report else root.parent/(root.name+f'_diagnostic_{radius}.json')
    if dest==stage/'latest.pt' or root==dest or root in dest.parents:
        raise ValueError('EMP_DIAG_REPORT_MUST_BE_OUTSIDE_CAMPAIGN')
    dest.parent.mkdir(parents=True,exist_ok=True)
    atomic_json(dest,report)
    primary={key:{bank:{'accuracy':round(v['within_5pct'],4),'scale_bias':round(v['scale_bias'],4) if v['scale_bias'] is not None else None,
                          'scale_stddev':round(v['scale_stddev'],4) if v['scale_stddev'] is not None else None,
                          'tipping':v['error_types']['tipping']} for bank,v in deck['summary'].items()}
             for key,deck in observed.items() if key.startswith(('frontier.','outer.'))}
    print('EMP_DIAG_SUMMARY='+json.dumps({'radius':radius,'step':state['step'],'revision':state['controls']['revision'],
           'report':str(dest),'by_view':primary},sort_keys=True),flush=True)
    for deck_name in ('frontier.dev_0','frontier.dev_1','outer.dev_0','outer.dev_1'):
        if deck_name not in observed:continue
        deck=observed[deck_name]
        hard=[]
        for axis in ('residual_shell','residual_sign','operand_shell','digits','input_signs'):
            for cohort,group in deck['groups'][axis].items():
                l=group['left.left'];r=group['right.right']
                if l['n']<4:continue
                hard.append({'dimension':axis,'cohort':cohort,'n':l['n'],
                             'left_accuracy':round(l['within_5pct'],4),
                             'right_accuracy':round(r['within_5pct'],4),
                             'gap':round(r['within_5pct']-l['within_5pct'],4),
                             'left_inward':l['error_types']['toward_zero'],
                             'left_tip':l['error_types']['tipping']})
        hard.sort(key=lambda x:(x['gap'],x['n']),reverse=True)
        print('EMP_DIAG_HARD_GROUPS='+json.dumps({'deck':deck_name,'groups':hard[:8]},sort_keys=True),flush=True)
    print('EMP_DIAG_CONTROLS='+json.dumps({'revision':report['controls_and_optimizer']['revision'],
        'banks':report['controls_and_optimizer']['banks']},sort_keys=True),flush=True)
    for key in ('frontier.dev_0','frontier.dev_1','outer.dev_0','outer.dev_1'):
        data=observed.get(key)
        if data:
            print('EMP_DIAG_PAIRED='+json.dumps({'deck':key,'counts':data['pair_left_left_vs_right_right'],
                 'within_emp_pairs':data['within_emp_pairs'],
                 'right_correct_left_wrong_examples':data['right_correct_left_wrong_examples'][:min(args.top,5)]},sort_keys=True),flush=True)
    if hard_report:
        overview = hard_report['summary']
        cohorts = []
        for dimension, groups in overview['cohorts'].items():
            for label, stats in groups.items():
                if stats['total'] < 16:
                    continue
                cohorts.append({'dimension': dimension, 'cohort': label, 'n': stats['total'],
                                'hard': stats['hard'],
                                'hard_rate': round(stats['hard']/stats['total'], 4),
                                'all_four_wrong': stats['all_four_wrong']})
        cohorts.sort(key=lambda x: (x['hard_rate'], x['hard'], x['n']), reverse=True)
        print('EMP_DIAG_HARD_CASES='+json.dumps({
            'radius': radius, 'step': state['step'],
            'checkpoint_sha256': checkpoint_hash,
            'export_path': hard_report['export_path'],
            'export_sha256': hard_report['export_sha256'],
            'scanned': overview['scanned'], 'eligible_hard': overview['eligible_hard'],
            'exported': overview['exported'],
            'breakdown': overview['breakdown'],
            'candidate_pair': overview['selected_pair'],
            'top_cohorts': cohorts[:8],
            'p90_abs_relative_error': {bank: round(stats['absolute_relative_error']['p90'], 4)
                                       if stats['absolute_relative_error']['p90'] is not None else None
                                       for bank, stats in hard_report['error_percentiles'].items()},
            'split': 'train', 'reserved_families_evaluated': 0}, sort_keys=True),flush=True)
    if hard_report and hard_report.get('detector'):
        d=hard_report['detector']
        dpath = root.parent / (root.name + f'_hard_detector_{radius}.json')
        atomic_json(dpath,d)
        print('EMP_DIAG_HARD_DETECTOR='+json.dumps({
            'radius':radius,'step':state['step'],'checkpoint_sha256':checkpoint_hash,
            'model_path':str(dpath),'target':d['label'],
            'train':d['train'],'evaluation':d['evaluation'],
            'predictive_signal':d['predictive_signal'],
            'unseen_anchor_rules':d['unseen_anchor_rules'][:5],
            'failure_signatures':d['holdout_failure_signatures'],
            'warning':d['warning']},sort_keys=True),flush=True)
    if matched_report:
        print('EMP_DIAG_MATCHED_CARRY='+json.dumps({
            'radius':radius,'step':state['step'],
            'checkpoint_sha256':checkpoint_hash,'selected_pair':list(chosen_pair),
            'groups':matched_report['groups'],
            'matching':matched_report['sampling']['matching'],
            'report':str(dest), 'warning':matched_report['warning']},sort_keys=True),flush=True)
    if report['parent_comparison']:
        pc=report['parent_comparison']
        print('EMP_DIAG_PARENT='+json.dumps({'radius':pc['accepted_radius'],
              'old_on_new':{k:{b:v[b]['within_5pct'] for b in ('left.left','right.right')} for k,v in pc['parent_on_current_radius'].items() if k.startswith('frontier.')},
              'now_on_old':{k:{b:v[b]['within_5pct'] for b in ('left.left','right.right')} for k,v in pc['current_on_parent_radius'].items() if k.startswith('frontier.')},
              'old_on_old':{k:{b:v[b]['within_5pct'] for b in ('left.left','right.right')} for k,v in pc['parent_on_parent_radius'].items() if k.startswith('frontier.')},
              },sort_keys=True),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,help='Existing outward ladder campaign root (read-only)')
    p.add_argument('--ladder-output',required=True,help='Original certified ladder architecture')
    p.add_argument('--radius',type=int,help='Defaults to latest stage with checkpoint')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--cpu-threads',type=int,default=2)
    p.add_argument('--top',type=int,default=12,help='Stored right-correct/left-wrong examples per deck')
    p.add_argument('--compare-parent',action='store_true',help='Also compare accepted preceding radius on identical DEV decks')
    p.add_argument('--report',help='Output JSON OUTSIDE campaign root')
    p.add_argument('--mine-hard',action='store_true',help='Identify/export difficult cases from fresh TRAIN-only examples')
    p.add_argument('--hard-candidates',type=int,default=8192,help='Size of deterministic TRAIN-only diagnostic pool')
    p.add_argument('--hard-max-cases',type=int,default=512,help='Maximum difficult examples exported')
    p.add_argument('--hard-per-anchor',type=int,default=8,help='Limit exported examples per TRAIN anchor family')
    p.add_argument('--hard-min-failures',type=int,default=2,help='Minimum banks failing a case to export (1..4)')
    p.add_argument('--hard-export',help='JSONL output path OUTSIDE campaign (default adjacent to campaign)')
    p.add_argument('--detect-hard',action='store_true',help='Train a diagnostic input-only risk detector on the mixed TRAIN pool and validate on unseen TRAIN anchors')
    p.add_argument('--detector-target',choices=detector.LABELS,default='both_selected_wrong',help='Failure condition the detector predicts')
    p.add_argument('--detector-depth',type=int,default=3,help='Maximum interpretable rule depth (1..4)')
    p.add_argument('--detector-min-leaf',type=int,default=48,help='Minimum examples per rule branch')
    p.add_argument('--matched-carry',action='store_true',help='Independent, frozen-checkpoint paired carry/borrow interventions using TRAIN anchors')
    p.add_argument('--matched-candidates',type=int,default=16384,help='Fresh TRAIN-only candidate pool for matched interventions')
    p.add_argument('--matched-pairs',type=int,default=128,help='Target paired examples per sign stratum')
    p.add_argument('--matched-max-target-drift',type=float,default=.20,help='Maximum within-pair relative target-magnitude drift')

    return p


if __name__=='__main__':
    args=parser().parse_args()
    if (args.hard_candidates < 1 or args.hard_max_cases < 1 or args.hard_per_anchor < 1
            or not 1 <= args.hard_min_failures <= 4):
        raise ValueError('EMP_DIAG_INVALID_HARD_ARGS')
    if not 1 <= args.detector_depth <= 4 or args.detector_min_leaf < 8:
        raise ValueError('EMP_DIAG_INVALID_DETECTOR_ARGS')
    if (args.matched_candidates < 1 or args.matched_pairs < 1
            or not 0 < args.matched_max_target_drift <= .5):
        raise ValueError('EMP_DIAG_INVALID_MATCHED_ARGS')
    if args.eval_batch_size<1 or args.cpu_threads<1 or args.top<0:
        raise ValueError('EMP_DIAG_INVALID_ARGS')
    run(args)
