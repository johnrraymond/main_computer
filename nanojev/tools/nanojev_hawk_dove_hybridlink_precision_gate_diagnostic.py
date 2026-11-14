#!/usr/bin/env python3
"""Read-only DEV audit: distinguish zero-gate mistakes from nonzero regression.

Evaluates the saved precision head at fixed zero thresholds, plus the ungated
head and an explicitly ORACLE-GATED diagnostic ceiling (not a deployable model).
No training, no checkpoint writes, no TEST scoring, and no changes to gates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import torch

from tools import nanojev_hawk_dove_hybridlink_curriculum as curriculum
from tools import nanojev_hawk_dove_hybridlink_format_diagnostic as diag
from tools import nanojev_hawk_dove_hybridlink_precision as precision
from tools import nanojev_hawk_dove_hybridlink_sequence as base
from tools import nanojev_hawk_dove_gaussian_population_conditional as gauss

SCHEMA = 'nanojev.hawk-dove.hybridlink-precision-gate-diagnostic.v1'
DEFAULT_THRESHOLDS = (.01, .1, .25, .5, .75, .9, .95, .99, .999, 1.)


def _score(pred, split, scale, tolerance, near_fraction):
    return {'strict': base.lab.strict_metrics(pred, split, tolerance),
            'strata': diag._xy_strata(pred, split, scale, tolerance,
                                      near_fraction)['by_stratum']}


def audit_predictions(regression, probabilities, split, scale, tolerance=.05,
                      near_fraction=.01, thresholds=DEFAULT_THRESHOLDS):
    """Raw predictions are normalized; truth is used ONLY to score diagnostics."""
    raw = regression.detach().cpu() * scale
    prob = probabilities.detach().cpu()
    truth = split['numeric'].detach().cpu()
    if raw.shape != truth.shape or prob.shape != truth.shape:
        raise ValueError('GATE_AUDIT_SHAPE_MISMATCH')
    if not all(0. <= t <= 1. for t in thresholds):
        raise ValueError('GATE_AUDIT_THRESHOLD_INVALID')
    truth_zero = truth == 0
    near_nonzero = (truth.abs() <= near_fraction * scale) & ~truth_zero
    far_nonzero = ~truth_zero & ~near_nonzero
    oracle_zero = torch.where(truth_zero, torch.zeros_like(raw), raw)
    baseline = _score(raw, split, scale, tolerance, near_fraction)
    ceiling = _score(oracle_zero, split, scale, tolerance, near_fraction)

    def _confusion(selected):
        return {
            'true_positive': int((selected & truth_zero).sum()),
            'false_positive': int((selected & ~truth_zero).sum()),
            'false_positive_near': int((selected & near_nonzero).sum()),
            'false_positive_away': int((selected & far_nonzero).sum()),
            'false_negative': int((~selected & truth_zero).sum()),
            'predicted_zero_coordinates': int(selected.sum()),
        }

    outcomes = []
    for threshold in thresholds:
        selected = prob >= threshold
        pred = torch.where(selected, torch.zeros_like(raw), raw)
        outcomes.append({'threshold':float(threshold), **_score(pred,split,scale,tolerance,near_fraction),
                         'zero_confusion':_confusion(selected)})
    ranked = max(outcomes, key=lambda row: (
        row['strict']['min_tier_joint5'],
        row['strata']['near_zero_nonzero']['joint_all_masks_rate'],
        -row['zero_confusion']['false_positive']))
    return {'ungated_raw':baseline,
            # Ground-truth zero flags are used here as an experimental ceiling ONLY.
            # This must never be called an autonomous inference result.
            'oracle_zero_gate_diagnostic_only':ceiling,
            'thresholds':outcomes,
            'best_dev_threshold_from_grid':ranked['threshold'],
            'true_zero_coordinates':int(truth_zero.sum()),
            'nonzero_near_coordinates':int(near_nonzero.sum()),
            'nonzero_away_coordinates':int(far_nonzero.sum())}


@torch.no_grad()
def collect(model, split, batch_size):
    model.eval()
    regression = []
    probabilities = []
    for x in split['features'].split(batch_size):
        pred, logits = model.raw_predictions(x)
        regression.append(pred.cpu())
        probabilities.append(logits.sigmoid().cpu())
    return torch.cat(regression),torch.cat(probabilities)


def run(args):
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('PRECISION_GATE_CUDA_UNAVAILABLE')
    if (args.eval_batch_size < 1 or args.cpu_threads < 1 or
            not 0 < args.tolerance < 1 or not 0 < args.near_fraction < 1):
        raise ValueError('PRECISION_GATE_BAD_OPTIONS')
    torch.set_num_threads(args.cpu_threads)
    precision_dir = args.precision_output.expanduser().resolve()
    config_path = precision_dir/'config.json'
    best = precision_dir/'best.pt'
    if not config_path.is_file() or not best.is_file():
        raise FileNotFoundError('PRECISION_GATE_BEST_OR_CONFIG_MISSING')
    config = json.loads(config_path.read_text(encoding='utf8'))
    if config.get('schema') != precision.SCHEMA:
        raise RuntimeError('PRECISION_GATE_CONFIG_SCHEMA_MISMATCH')
    source, format_checkpoint, _, original_identity = precision._input_config(
        SimpleNamespace(reference_checkpoint=args.reference_checkpoint,
                        curriculum_output=args.curriculum_output,
                        output=precision_dir,
                        arm=config['options']['arm']))
    if config['reference_sha256'] != base.file_sha256(source) or\
       config['format_checkpoint_sha256'] != base.file_sha256(format_checkpoint) or\
       config['curriculum_identity'] != original_identity:
        raise RuntimeError('PRECISION_GATE_SOURCE_OR_FORMAT_DRIFT')
    source_sha = base.file_sha256(source)
    fmt_sha = base.file_sha256(format_checkpoint)
    best_sha = base.file_sha256(best)
    reference, splits, _, original_config = base.load_source(source,args.device)
    options = SimpleNamespace(**original_identity['options'])
    if curriculum.make_identity(options,source,original_config)!=original_identity:
        raise RuntimeError('PRECISION_GATE_CURRICULUM_IDENTITY_DRIFT')
    model = precision.PrecisionAdapter(
        curriculum.CurriculumModel(reference,options.heads,options.layers)).to(args.device)
    snapshot = torch.load(best,map_location='cpu',weights_only=True)
    if snapshot.get('schema') != precision.SCHEMA+'.best' or snapshot.get('identity')!=config:
        raise RuntimeError('PRECISION_GATE_BEST_IDENTITY_DRIFT')
    model.load_state_dict(snapshot['model'],strict=True)
    scale = base.xy_scale(original_config['source_identity']['payoff_bound'])
    regression, probability = collect(model,splits['dev'],args.eval_batch_size)
    audit = audit_predictions(regression,probability,splits['dev'],scale,
                              args.tolerance,args.near_fraction)
    result = {'schema':SCHEMA,'checkpoint_step':snapshot['step'],
              'checkpoint_sha256':best_sha,
              'scope':config['options']['scope'],
              'data_policy':'DEV evaluation only; no TEST evaluation',
              'oracle_gate_policy':'diagnostic ceiling only; uses ground-truth zeros; NOT inference',
              'evaluation':audit}
    if (base.file_sha256(source)!=source_sha or
        base.file_sha256(format_checkpoint)!=fmt_sha or
        base.file_sha256(best)!=best_sha):
        raise RuntimeError('PRECISION_GATE_SOURCE_CHANGED')
    if args.report:
        out = args.report.expanduser().resolve()
        if (precision_dir not in out.parents or out.suffix.lower() != '.json' or
                out in (source,format_checkpoint,best,config_path)):
            raise RuntimeError('PRECISION_GATE_REPORT_MUST_BE_NEW_JSON_INSIDE_PRECISION_OUTPUT')
        gauss._atomic_json(out,result)
    print('PRECISION_GATE_AUDIT='+json.dumps({
        'step':snapshot['step'],
        'ungated':{'min_tier':audit['ungated_raw']['strict']['min_tier_joint5'],
                   'near':audit['ungated_raw']['strata']['near_zero_nonzero']['joint_all_masks_rate']},
        'oracle_gate_ceiling':{
            'min_tier':audit['oracle_zero_gate_diagnostic_only']['strict']['min_tier_joint5'],
            'near':audit['oracle_zero_gate_diagnostic_only']['strata']['near_zero_nonzero']['joint_all_masks_rate']},
        'threshold_grid':[{'threshold':row['threshold'],
                           'min_tier':row['strict']['min_tier_joint5'],
                           'near':row['strata']['near_zero_nonzero']['joint_all_masks_rate'],
                           'exact_zero':row['strata']['exact_zero']['joint_all_masks_rate'],
                           'false_zero':row['zero_confusion']['false_positive']}
                          for row in audit['thresholds']],
        'best_dev_threshold':audit['best_dev_threshold_from_grid'],
        'report':str(args.report) if args.report else None}, sort_keys=True),flush=True)
    print('PRECISION_GATE_READ_ONLY=TRUE TEST_UNTOUCHED=TRUE '
          'ORIGINAL_GATES_UNCHANGED=TRUE',flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-checkpoint',type=Path,required=True)
    p.add_argument('--curriculum-output',type=Path,required=True)
    p.add_argument('--precision-output',type=Path,required=True)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--near-fraction',type=float,default=.01)
    p.add_argument('--tolerance',type=float,default=.05)
    p.add_argument('--report',type=Path)
    return p


def main(argv=None):
    return run(parser().parse_args(argv))


if __name__=='__main__':
    main()
