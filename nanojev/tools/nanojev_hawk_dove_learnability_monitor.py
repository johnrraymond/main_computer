#!/usr/bin/env python3
"""Read-only, fail-honest 21-bank learnability / optimizer / A-B diagnostic.

Reads only completed probe-step-*.json and optional config.json. Never loads
model weights, invokes an oracle, opens TEST, or changes training artifacts.
Older v1 learnability probes remain readable; genuinely absent evidence is
reported as NOT_MEASURED, never inferred from accuracy or loss values.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

SCHEMA = 'nanojev.hawk-dove.nested90-expected.learnability-monitor.v2'
BANDS = tuple(str(i) for i in range(8))
CRITICAL_CONFIG = (
    'stage2_sha256', 'original_parent_sha256', 'donor_sha256',
    'decoder_init_sha256', 'bank_init_sha256', 'source_sha256',
    'teacher_sha256', 'mixture_fixture_sha256', 'mixture_plan_sha256',
    'mixture_generated_train_rows_sha256', 'mixture_generated_dev_rows_sha256',
    'learnability_train_per_tier', 'seed', 'views', 'lr',
    'small_threshold', 'small_boost', 'numeric_weight', 'head_weight',
    'gaussian_weight', 'retention_fraction', 'relative_max', 'bank_count',
)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    x = float(value)
    return x if math.isfinite(x) else None


def _value(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return _number(obj)


def _difference(a, b):
    return a - b if a is not None and b is not None else None


def _band(data, i):
    band = data.get(str(i)) if isinstance(data, dict) else None
    if not isinstance(band, dict):
        band = {}
    model = _value(band, 'mean_absolute_asinh_error')
    zero = _value(band, 'zero_predictor_mean_absolute_asinh_error')
    frozen = _value(band, 'frozen_mean_absolute_asinh_error')
    return {
        'n': band.get('n') if isinstance(band.get('n'), int) else None,
        'model_asinh_mae': model,
        'zero_asinh_mae': zero,
        'frozen_asinh_mae': frozen,
        'gain_over_zero_asinh_mae': _difference(zero, model),
        'gain_over_frozen_asinh_mae': _difference(frozen, model),
        'strict_relative_5pct_nonzero': _value(band, 'strict_relative_accuracy_5pct_nonzero'),
        'accuracy_5pct_floor': _value(band, 'accuracy_5pct_floor'),
        'zero_accuracy_5pct_floor': _value(band, 'zero_predictor_accuracy_5pct_floor'),
        'frozen_accuracy_5pct_floor': _value(band, 'frozen_accuracy_5pct_floor'),
    }


def _record(p):
    learning = p.get('learnability') or {}
    losses = p.get('chunk_mean_train_losses_and_gradients') or {}
    new = p.get('nested90_unseen_dev') or {}
    bands = new.get('magnitude_bands') or {}
    train_bands = p.get('magnitude_bands_train') or {}
    sub = _band(bands, 7)
    train_sub = _band(train_bands, 7)
    new_mae = _value(learning, 'fractional_new_dev_mae_asinh')
    new_zero = _value(learning, 'fractional_new_dev_zero_mae_asinh')
    new_frozen = _value(learning, 'fractional_new_dev_frozen_mae_asinh')
    if new_mae is None:
        new_mae = _value(new, 'mean_absolute_asinh_error')
    if new_zero is None:
        new_zero = _value(new, 'zero_predictor_mean_absolute_asinh_error')
    if new_frozen is None:
        new_frozen = _value(new, 'frozen_mean_absolute_asinh_error')
    return {
        'step': p['step'], 'arm': learning.get('arm'),
        'fractional_train_asinh_mae': _value(learning, 'fractional_train_mae_asinh'),
        'fractional_new_dev_asinh_mae': new_mae,
        'new_dev_zero_asinh_mae': new_zero,
        'new_dev_frozen_asinh_mae': new_frozen,
        'new_dev_gain_over_zero_asinh_mae': _difference(new_zero, new_mae),
        'new_dev_gain_over_frozen_asinh_mae': _difference(new_frozen, new_mae),
        'new_dev_accuracy': _value(new, 'joint', 'accuracy_5pct'),
        'new_dev_subunit_strict_relative_5pct': sub['strict_relative_5pct_nonzero'],
        'train_subunit_strict_relative_5pct': train_sub['strict_relative_5pct_nonzero'],
        'original_dev_accuracy': _value(p, 'joint', 'accuracy_5pct'),
        'original_small_accuracy': _value(p, 'joint', 'small_accuracy_5pct'),
        'new_dev_subunit_zero_baseline_5pct_floor': sub['zero_accuracy_5pct_floor'],
        'new_dev_subunit_model_5pct_floor': sub['accuracy_5pct_floor'],
        'new_dev_subunit_frozen_5pct_floor': sub['frozen_accuracy_5pct_floor'],
        'new_dev_subunit_model_asinh_mae': sub['model_asinh_mae'],
        'new_dev_subunit_zero_asinh_mae': sub['zero_asinh_mae'],
        'new_dev_subunit_gain_over_zero_asinh_mae': sub['gain_over_zero_asinh_mae'],
        'train_subunit_model_asinh_mae': train_sub['model_asinh_mae'],
        'mean_gradient_global_norm_before_clip': _value(losses, 'gradient_global_norm_before_clip'),
        'fraction_steps_clipped': _value(losses, 'gradient_clip_fraction'),
        'mean_clip_multiplier': _value(losses, 'gradient_clip_multiplier'),
        'mean_decoder_delta_abs': _value(losses, 'decoder_delta_abs'),
        'mean_numeric_head_gradient_norm': _value(losses, 'numeric_gradient_head_norm'),
        'gaussian_bank_bootstrap_used': _value(losses, 'gaussian_bank_bootstrap_used'),
        'bands_new_dev': {k: _band(bands, k) for k in BANDS},
        'bands_train': {k: _band(train_bands, k) for k in BANDS},
        'per_tier_new_dev_asinh_mae': new.get('per_tier_absolute_asinh_error'),
    }


def _trend(first, last):
    train = _difference(first.get('fractional_train_asinh_mae'), last.get('fractional_train_asinh_mae'))
    dev = _difference(first.get('fractional_new_dev_asinh_mae'), last.get('fractional_new_dev_asinh_mae'))
    if train is None or dev is None:
        return 'INSUFFICIENT_EVIDENCE', train, dev
    if train > 0 and dev > 0:
        return 'TRAIN_AND_HELD_OUT_ERROR_DECREASING', train, dev
    if train > 0 and dev <= 0:
        return 'TRAIN_FIT_WITHOUT_HELD_OUT_GAIN', train, dev
    if train <= 0:
        return 'TRAIN_ERROR_NOT_DECREASING', train, dev
    return 'UNDETERMINED', train, dev


def _learnability_signals(history, clip_warn_fraction):
    first, latest = history[0], history[-1]
    observed = [x for x in history if x['step'] > 0]
    warnings = []
    signals = []
    if not observed:
        return 'NOT_YET_TRAINED', ['ONLY_INITIAL_PROBE'], []
    assessment, train_gain, dev_gain = _trend(first, latest)
    if assessment == 'TRAIN_AND_HELD_OUT_ERROR_DECREASING':
        signals.append('TRAIN_AND_UNSEEN_TRANSFORMED_ERROR_REDUCED')
    elif assessment == 'TRAIN_FIT_WITHOUT_HELD_OUT_GAIN':
        warnings.append('FIT_WITHOUT_HELD_OUT_IMPROVEMENT')
    elif assessment == 'TRAIN_ERROR_NOT_DECREASING':
        warnings.append('NO_ENDPOINT_TRAIN_FIT')
    else:
        warnings.append('MISSING_TRAIN_OR_DEV_ERROR')
    strict0 = first.get('new_dev_subunit_strict_relative_5pct')
    strict1 = latest.get('new_dev_subunit_strict_relative_5pct')
    if strict0 is not None and strict1 is not None and strict1 > strict0:
        signals.append('STRICT_NONZERO_SUBUNIT_ACCURACY_IMPROVED')
    else:
        warnings.append('STRICT_SUBUNIT_IMPROVEMENT_NOT_ESTABLISHED')
    sub_zero = latest.get('new_dev_subunit_gain_over_zero_asinh_mae')
    if sub_zero is None:
        warnings.append('ZERO_ASINH_BASELINE_NOT_MEASURED_IN_HISTORICAL_PROBE')
    elif sub_zero <= 0:
        warnings.append('SUBUNIT_ASINH_NOT_BETTER_THAN_ZERO')
    else:
        signals.append('SUBUNIT_ASINH_BETTER_THAN_ZERO')
    frozen_margin = latest.get('new_dev_gain_over_frozen_asinh_mae')
    if frozen_margin is None:
        warnings.append('FROZEN_TRANSFORMED_BASELINE_NOT_MEASURED')
    elif frozen_margin<=0:
        warnings.append('HELD_OUT_ASINH_NOT_BETTER_THAN_FROZEN')
    else:
        signals.append('HELD_OUT_ASINH_BETTER_THAN_FROZEN')
    whole_zero = latest.get('new_dev_gain_over_zero_asinh_mae')
    if whole_zero is None:
        warnings.append('WHOLE_DEV_ZERO_ASINH_BASELINE_NOT_MEASURED')
    elif whole_zero <= 0:
        warnings.append('WHOLE_DEV_ASINH_NOT_BETTER_THAN_ZERO')
    else:
        signals.append('WHOLE_DEV_ASINH_BETTER_THAN_ZERO')
    recent_clips = [r['fraction_steps_clipped'] for r in observed[-3:]
                    if r['fraction_steps_clipped'] is not None]
    if recent_clips and all(x >= clip_warn_fraction for x in recent_clips):
        warnings.append('PERSISTENT_GRADIENT_CLIPPING')
    if (first.get('original_dev_accuracy') is not None and latest.get('original_dev_accuracy') is not None and
        first['original_dev_accuracy']-latest['original_dev_accuracy']>=.20):
        warnings.append('ORIGINAL_TASK_ACCURACY_DROP_OBSERVED_NOT_AN_ABORT')
    elif not recent_clips:
        warnings.append('CLIPPING_FRACTION_NOT_MEASURED')
    if latest.get('new_dev_gain_over_zero_asinh_mae') is not None and \
            latest.get('new_dev_subunit_gain_over_zero_asinh_mae') is not None:
        if dev_gain is not None and dev_gain>0 and strict1 is not None and strict0 is not None and strict1>strict0 and \
                latest['new_dev_subunit_gain_over_zero_asinh_mae'] > 0 and \
                latest['new_dev_gain_over_zero_asinh_mae'] > 0:
            verdict = 'EVIDENCE_OF_HELD_OUT_NUMERICAL_LEARNING'
        elif dev_gain is not None and dev_gain>0:
            verdict = 'HELD_OUT_ASINH_IMPROVING_BUT_NUMERICAL_LEARNING_NOT_ESTABLISHED'
        else:
            verdict = 'NO_HELD_OUT_NUMERICAL_LEARNING_ESTABLISHED'
    else:
        verdict = 'ZERO_BASELINE_EVIDENCE_MISSING'
    return verdict, warnings, signals


def review(run_dir, *, clip_warn_fraction=.95, history_limit=40):
    root = Path(run_dir).expanduser().resolve()
    paths = list(root.glob('probe-step-*.json'))
    if not paths:
        return {'schema':SCHEMA, 'run_dir':str(root), 'status':'NO_PROBES',
                'history':[], 'read_only':True, 'test_split_read':False}
    parsed = []
    for path in paths:
        try:
            p = json.loads(path.read_text(encoding='utf8'))
        except (OSError, ValueError) as exc:
            raise ValueError('LEARNABILITY_INVALID_PROBE:'+str(path)) from exc
        if not isinstance(p, dict) or '.learnability.v1.probe.v1' not in p.get('schema',''):
            raise ValueError('LEARNABILITY_UNEXPECTED_PROBE_SCHEMA:'+str(path))
        s = p.get('step')
        if isinstance(s,bool) or not isinstance(s,int) or s<0:
            raise ValueError('LEARNABILITY_INVALID_STEP')
        match = re.fullmatch(r'probe-step-(\d+)\.json', path.name)
        if not match or int(match.group(1)) != s:
            raise ValueError('LEARNABILITY_FILENAME_STEP_MISMATCH:'+str(path))
        parsed.append(p)
    parsed.sort(key=lambda item:item['step'])
    steps = [p['step'] for p in parsed]
    if len(steps) != len(set(steps)):
        raise ValueError('LEARNABILITY_DUPLICATE_STEP_PROBES')
    if not isinstance(history_limit,int) or history_limit<1:
        raise ValueError('LEARNABILITY_BAD_HISTORY_LIMIT')
    history = [_record(p) for p in parsed]
    first, latest = history[0],history[-1]
    assessment,train_gain,dev_gain = _trend(first,latest) if len(history)>1 else ('INSUFFICIENT_EVIDENCE',None,None)
    verdict,warnings,signals = _learnability_signals(history,clip_warn_fraction)
    valid_dev = [(row['step'],row['fractional_new_dev_asinh_mae']) for row in history
                 if row['fractional_new_dev_asinh_mae'] is not None]
    best_step,best_score = min(valid_dev,key=lambda x:x[1]) if valid_dev else (None,None)
    cfg_path = root/'config.json'
    config=None
    if cfg_path.is_file():
        try:
            config=json.loads(cfg_path.read_text(encoding='utf8'))
            if not isinstance(config,dict):raise ValueError('config_not_object')
        except (OSError,ValueError) as exc:
            raise ValueError('LEARNABILITY_INVALID_CONFIG:'+str(cfg_path)) from exc
    status = 'WARN' if warnings else ('OBSERVING' if len(history)<2 else 'OK')
    return {'schema':SCHEMA,'run_dir':str(root),'status':status,
            'assessment':assessment,'learnability_verdict':verdict,
            'train_asinh_mae_reduction':train_gain,
            'held_out_asinh_mae_reduction':dev_gain,
            'zero_baseline_gap_latest':{
                'whole_new_dev_asinh': latest['new_dev_gain_over_zero_asinh_mae'],
                'subunit_new_dev_asinh':latest['new_dev_subunit_gain_over_zero_asinh_mae'],
                'subunit_strict_relative_5pct':latest['new_dev_subunit_strict_relative_5pct'],
                'subunit_absolute_floor_5pct':latest['new_dev_subunit_model_5pct_floor'],
                'subunit_zero_absolute_floor_5pct':latest['new_dev_subunit_zero_baseline_5pct_floor']},
            'warnings':warnings,'learning_signals':signals,
            'first':first,'latest':latest,
            'history':[ {k:v for k,v in row.items() if k not in ('bands_new_dev','bands_train','per_tier_new_dev_asinh_mae')}
                        for row in history[-history_limit:]],
            'history_length_total':len(history),
            'history_truncated':len(history)>history_limit,
            'best_held_out_asinh_mae':best_score, 'best_held_out_step':best_step,
            'last_step_is_best':best_step == latest['step'],
            'config_identity':config,
            'test_split_read':False,'read_only':True}


def _pairwise_comparison(a,b):
    if a['status']=='NO_PROBES' or b['status']=='NO_PROBES':
        return {'status':'NOT_COMPARABLE','reason':'MISSING_PROBES'}
    ca,cb=a['config_identity'],b['config_identity']
    if ca is None or cb is None:
        return {'status':'NOT_COMPARABLE','reason':'MISSING_CONFIG_IDENTITY'}
    if ca.get('experiment_arm') == cb.get('experiment_arm'):
        return {'status':'NOT_COMPARABLE','reason':'NOT_DIFFERENT_ARMS'}
    missing=[key for key in CRITICAL_CONFIG if key not in ca or key not in cb]
    if missing:
        return {'status':'NOT_COMPARABLE','reason':'CONFIG_IDENTITY_INCOMPLETE',
                'missing_fields':missing}
    different=[key for key in CRITICAL_CONFIG if ca[key]!=cb[key]]
    if different:
        return {'status':'NOT_COMPARABLE','reason':'DIFFERENT_TRAINING_OR_DATA_IDENTITY',
                'different_fields':different}
    common={r['step']:r for r in a['history']}
    common[a['first']['step']]=a['first']
    other={r['step']:r for r in b['history']}
    other[b['first']['step']]=b['first']
    overlap=sorted(set(common).intersection(other))
    if not overlap or overlap[0]!=0:
        return {'status':'NOT_COMPARABLE','reason':'NO_COMMON_STEP_ZERO'}
    # Different seed/model initializations can be seen at step 0 even if
    # identifying configs accidentally match. Do not rank such arms.
    ra,rb=common[0],other[0]
    for key in ('fractional_train_asinh_mae','fractional_new_dev_asinh_mae','original_dev_accuracy'):
        va,vb=ra.get(key),rb.get(key)
        if va is None or vb is None or abs(va-vb)>1e-5:
            return {'status':'NOT_COMPARABLE','reason':'DIFFERENT_INITIAL_EVALUATIONS','field':key,
                    'first_arm_value':va,'second_arm_value':vb}
    last_step=max(overlap)
    end1=common[last_step]
    end2=other[last_step]
    ae=end1['fractional_new_dev_asinh_mae']
    be=end2['fractional_new_dev_asinh_mae']
    winner=None if ae is None or be is None or abs(ae-be)<1e-9 else (
        end1['arm'] if ae<be else end2['arm'])
    return {'status':'COMPARABLE','comparison_step':last_step,
            'arms':[end1['arm'],end2['arm']],
            'held_out_asinh_mae':[ae,be],
            'lower_held_out_asinh_mae_arm':winner,
            'subunit_strict_relative_5pct':[end1['new_dev_subunit_strict_relative_5pct'],
                                             end2['new_dev_subunit_strict_relative_5pct']],
            'note':'Same training initialization and data; comparison is at common probe step only. Lower MAE does not prove the numerical rule was learned.'}


def compare(directories, *, clip_warn_fraction=.95, history_limit=40):
    reports=[review(p,clip_warn_fraction=clip_warn_fraction,history_limit=history_limit) for p in directories]
    out={'schema':SCHEMA+'.comparison','runs':reports,'read_only':True,'test_split_read':False}
    if len(reports)==2:
        out['ab_comparison']=_pairwise_comparison(reports[0],reports[1])
    return out


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,action='append',required=True,
                   help='Once for one experiment; twice for controlled decoder-only versus joint')
    p.add_argument('--clip-warn-fraction',type=float,default=.95,
                   help='Warn if each of the last three available non-initial chunks clips this often (default 0.95)')
    p.add_argument('--history-limit',type=int,default=40,
                   help='Bound history in output; first/latest/best still cover all probes (default 40)')
    args=p.parse_args(argv)
    if args.history_limit<1:
        p.error('--history-limit must be >=1')
    if not (0<=args.clip_warn_fraction<=1):
        p.error('--clip-warn-fraction must be in [0,1]')
    print(json.dumps(compare(args.run_dir,clip_warn_fraction=args.clip_warn_fraction,history_limit=args.history_limit),
                     indent=2,sort_keys=True,allow_nan=False))

if __name__=='__main__':
    main()
