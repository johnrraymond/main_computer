#!/usr/bin/env python3
"""Read-only structural learning monitor for the 21 Gaussian banks, not a decoder monitor."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

SCHEMA='nanojev.hawk-dove.gaussian-bank-21.monitor.v1'


def _change(before,after):
    if before is None or after is None:return None
    return before-after


def review(run_dir):
    run_dir=Path(run_dir)
    paths=sorted(run_dir.glob('probe-step-*.json'))
    if not paths:
        return {'schema':SCHEMA,'status':'NO_PROBES','run_dir':str(run_dir),'read_only':True}
    history=[]
    for path in paths:
        row=json.loads(path.read_text('utf8'))
        if not row.get('schema','').endswith('direct-supervision.v1.probe.v1'):
            raise ValueError('BANK_MONITOR_WRONG_PROBE_SCHEMA:'+str(path))
        if row.get('test_split_read') is not False or row.get('decoder_trained') is not False:
            raise ValueError('BANK_MONITOR_UNEXPECTED_PROBE_ACCESS_OR_TRAINING')
        if len(row['held_out_dev']['per_bank']) != 21:
            raise ValueError('BANK_MONITOR_WRONG_BANK_COUNT')
        if history and int(row['step'])<=int(history[-1]['step']):
            raise ValueError('BANK_MONITOR_STEPS_NOT_INCREASING')
        history.append(row)
    first,last=history[0],history[-1]
    banks=[]
    for index in range(21):
        a,b=first['held_out_dev']['per_bank'][index],last['held_out_dev']['per_bank'][index]
        tr=last['train']['per_bank'][index]
        if a['bank']!=b['bank'] or b['bank']!=tr['bank']:
            raise ValueError('BANK_MONITOR_BANK_ID_DRIFT')
        train_loss=tr['balanced_region_kl'];dev_loss=b['balanced_region_kl']
        dev_initial=a['balanced_region_kl']
        support={name:b['regions'][name]['n'] for name in ('lhs','interior','rhs')}
        gains={name:_change(a['regions'][name]['mean_kl'],b['regions'][name]['mean_kl'])
               for name in ('lhs','interior','rhs')}
        has_update=tr['weight_change_l2']>1e-12
        trend=_change(dev_initial,dev_loss)
        if not has_update:
            outcome='NO_BANK_WEIGHT_UPDATE'
        elif trend>1e-7:
            outcome='HELD_OUT_DISTRIBUTION_IMPROVING'
        elif trend< -1e-7:
            outcome='HELD_OUT_DISTRIBUTION_WORSENING'
        else:outcome='NO_CLEAR_HELD_OUT_KL_CHANGE'
        banks.append({'bank':b['bank'],'stage':b['stage'],'radius':b['radius'],
           'training_region_balanced_kl':train_loss,
           'held_out_region_balanced_kl':dev_loss,
           'initial_held_out_region_balanced_kl':dev_initial,
           'held_out_kl_reduction':trend,
           'held_out_region_kl_reduction':gains,
           'held_out_support':support,
           'held_out_interior_center_error_bins':b['regions']['interior']['mean_oracle_center_error_bins'],
           'held_out_interior_mass':b['regions']['interior']['mean_interior_mass'],
           'held_out_conditional_gaussian_shape_kl':b['regions']['interior'].get('conditional_gaussian_shape_kl'),
           'held_out_ordered_oracle_pairs':b['regions']['interior']['ordered_oracle_pairs'],
           'held_out_center_order_violation_fraction':b['regions']['interior']['center_order_violation_fraction'],
           'weight_change_l2':tr['weight_change_l2'],
           'assessment':outcome,
           'coverage_warning':[name for name in ('lhs','interior','rhs') if support[name]==0]})
    overall=[{'step':p['step'],
              'train_bank_kl':p['train']['balanced_mean_bank_kl'],
              'held_out_bank_kl':p['held_out_dev']['balanced_mean_bank_kl'],
              'train_interior_banks_supported':p['train']['banks_with_interior_support'],
              'held_out_interior_banks_supported':p['held_out_dev']['banks_with_interior_support'],
              'chunk_mean_banks_clipped':p.get('chunk',{}).get('mean_banks_clipped') if p.get('chunk') else None,
              'chunk_mean_banks_with_nonzero_gradient':p.get('chunk',{}).get('mean_banks_with_nonzero_gradient') if p.get('chunk') else None}
              for p in history]
    return {'schema':SCHEMA,'status':'OK','read_only':True,'run_dir':str(run_dir),
        'first_step':first['step'],'step':last['step'],'probe_count':len(history),
        'target':'learn_LHS_Gaussian_RHS_oracle_distributions',
        'decoder_accuracy_not_evaluated':True,
        'train_bank_kl':last['train']['balanced_mean_bank_kl'],
        'held_out_bank_kl':last['held_out_dev']['balanced_mean_bank_kl'],
        'held_out_bank_kl_reduction':_change(first['held_out_dev']['balanced_mean_bank_kl'],
                                             last['held_out_dev']['balanced_mean_bank_kl']),
        'banks_with_measurable_weight_updates':sum(row['weight_change_l2']>1e-12 for row in banks),
        'banks_held_out_kl_improving':sum(row['assessment']=='HELD_OUT_DISTRIBUTION_IMPROVING' for row in banks),
        'banks_with_held_out_interior_support':sum(row['held_out_support']['interior']>0 for row in banks),
        'banks_with_held_out_lhs_support':sum(row['held_out_support']['lhs']>0 for row in banks),
        'banks_with_held_out_rhs_support':sum(row['held_out_support']['rhs']>0 for row in banks),
        'warning':'Missing regions have no evidence of learned behavior; they are not scored as success.',
        'banks':banks,'history':overall}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,default=__import__(
        'tools.nanojev_hawk_dove_gaussian_bank_21_supervision',fromlist=['DEFAULT_OUTPUT']).DEFAULT_OUTPUT)
    p.add_argument('--deterministic-test',action='store_true',
                   help='Opt in to frozen-Qwen DEV inference: deterministic bank X, derived Y and Z (no training)')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--samples-per-band',type=int,default=6,
                   help='Number of held-out examples per tier per 1-10/10-100/100-1000 band')
    p.add_argument('--show-cases',type=int,default=12)
    p.add_argument('--gate-ablation',action='store_true',
                   help='Compare existing hard overflow gates against a shadow estimate without hard-bound exclusions, counting all errors')
    p.add_argument('--source',type=Path,default=None)
    p.add_argument('--teacher',type=Path,default=None)
    p.add_argument('--mixture-fixture',type=Path,default=None)
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    report=review(args.run_dir)
    if args.gate_ablation and not args.deterministic_test:
        raise ValueError('BANK_MONITOR_GATE_ABLATION_REQUIRES_DETERMINISTIC_TEST')
    if args.deterministic_test:
        if report['status']!='OK':
            raise ValueError('BANK_MONITOR_REQUIRES_EXISTING_PROBES_FOR_DETERMINISTIC_TEST')
        from tools.nanojev_hawk_dove_gaussian_bank_21_deterministic import evaluate_checkpoint
        report['deterministic_test']=evaluate_checkpoint(args.run_dir,device=args.device,
            dtype=args.dtype,per_band=args.samples_per_band,
            allow_model_download=args.allow_model_download,
            source=args.source,teacher=args.teacher,fixture=args.mixture_fixture,
            show_cases=args.show_cases,gate_ablation=args.gate_ablation)
    print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))

if __name__=='__main__':main()
