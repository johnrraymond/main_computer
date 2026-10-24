#!/usr/bin/env python3
"""Read-only, case-level failure diagnosis for blind-matrix Hawk–Dove checkpoints.

Reconstructs precisely the matrix-held-out dataset bound to the checkpoint.
Oracle X/Y are used ONLY to score frozen predictions, never to build features.
DEV is the default diagnostic split. Examining TEST is explicitly opt-in and
invalidates treating that same TEST as a fresh untouched holdout for later work.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import math
from pathlib import Path
import statistics

import torch

from tools import nanojev_hawk_dove_gaussian_population_blind_matrix as bm
from tools import nanojev_hawk_dove_gaussian_population_conditional as base

SCHEMA = bm.SCHEMA + '.failure-diagnostics.v1'
CASE_FIELDS = (
    'split','tier','mask','instance','matrix_index','matrix_sha256',
    'hawk_count','dove_count','HH','HD','DH','DD',
    'true_X','true_Y','pred_X','pred_Y','center_X','center_Y',
    'X_absolute_error','Y_absolute_error','X_relative_error','Y_relative_error',
    'X_allowed_error','Y_allowed_error','X_pass','Y_pass','XY_pass',
    'center_X_pass','center_Y_pass','center_XY_pass',
    'X_bank_shift','Y_bank_shift','X_center_absolute_error','Y_center_absolute_error',
    'density_tv','density_kl','true_Z','pred_Z','Z_pass',
    'min_oracle_magnitude','magnitude_bucket','count_bucket','root_proximity',
)


def magnitude_bucket(value):
    value = abs(value)
    if value == 0: return 'zero'
    bounds=((1,'(0,1]'),(10,'(1,10]'),(100,'(10,100]'),
            (1000,'(100,1000]'),(10000,'(1000,10000]'),
            (100000,'(10000,100000]'))
    for bound,label in bounds:
        if value <= bound:return label
    return '>100000'


def count_bucket(a):
    if a in (0,bm.TOTAL): return 'endpoint'
    if a <= 1000 or a >= bm.TOTAL-1000: return 'near_endpoint'
    return 'interior'


def root_proximity(a,matrix):
    hh,hd,dh,dd=matrix
    formulas=(('X_zero',-hd*bm.TOTAL,hh-hd),
              ('Y_zero',-dd*bm.TOTAL,dh-dd),
              ('X_equals_Y',(dd-hd)*bm.TOTAL,hh-hd-dh+dd))
    closest=None
    for name,num,den in formulas:
        if not den and not num:
            return name+':identically_zero'
        if den:
            root=num/den
            if 0 <= root <= bm.TOTAL:
                distance=abs(a-root)
                if closest is None or distance < closest[1]:
                    closest=(name,distance)
    if closest is None:return 'no_in_range_root'
    if closest[1] < 1e-9:return closest[0]+':exact'
    if closest[1] <= 2.00000001:return closest[0]+':within_2_counts'
    if closest[1] <= 1000:return closest[0]+':within_1000_counts'
    return 'distant_from_roots'


def _hit(pred, truth, tolerance):
    return abs(pred-truth) <= tolerance*abs(truth)


def _label(x,y):
    return 'HAWK' if x>y else 'DOVE' if x<y else 'TIE'


def _single_row(row,pred,proposal,tv,kl,split,matrix_hash,tolerance):
    visible=bm.parse_blind(row['prompt'],row['tier'],row['mask'])
    a=visible['a']; b=visible['b']; hh,hd,dh,dd=visible['payoffs']
    expected_x=a*hh+b*hd; expected_y=a*dh+b*dd
    if expected_x != row['x'] or expected_y != row['y']:
        raise ValueError('BLIND_DIAG_ORACLE_DATA_INCONSISTENCY')
    x,y=float(row['x']),float(row['y'])
    px,py=map(float,pred);cx,cy=map(float,proposal)
    if not all(map(math.isfinite,(px,py,cx,cy,tv,kl))):
        raise FloatingPointError('BLIND_DIAG_NONFINITE_PREDICTION')
    ex,ey=abs(px-x),abs(py-y)
    xok,yok=_hit(px,x,tolerance),_hit(py,y,tolerance)
    cxok,cyok=_hit(cx,x,tolerance),_hit(cy,y,tolerance)
    return {
        'split':split,'tier':row['tier'],'mask':row['mask'],
        'instance':row['instance'],'matrix_index':row['matrix_index'],
        'matrix_sha256':matrix_hash,'hawk_count':a,'dove_count':b,
        'HH':hh,'HD':hd,'DH':dh,'DD':dd,
        'true_X':x,'true_Y':y,'pred_X':px,'pred_Y':py,'center_X':cx,'center_Y':cy,
        'X_absolute_error':ex,'Y_absolute_error':ey,
        'X_relative_error':ex/abs(x) if x else None,
        'Y_relative_error':ey/abs(y) if y else None,
        'X_allowed_error':tolerance*abs(x),'Y_allowed_error':tolerance*abs(y),
        'X_pass':xok,'Y_pass':yok,'XY_pass':xok and yok,
        'center_X_pass':cxok,'center_Y_pass':cyok,'center_XY_pass':cxok and cyok,
        'X_bank_shift':px-cx,'Y_bank_shift':py-cy,
        'X_center_absolute_error':abs(cx-x),'Y_center_absolute_error':abs(cy-y),
        'density_tv':float(tv),'density_kl':float(kl),
        'true_Z':_label(x,y),'pred_Z':_label(px,py),
        'Z_pass':_label(x,y)==_label(px,py),
        'min_oracle_magnitude':min(abs(x),abs(y)),
        'magnitude_bucket':magnitude_bucket(min(abs(x),abs(y))),
        'count_bucket':count_bucket(a),'root_proximity':root_proximity(a,(hh,hd,dh,dd)),
    }


@torch.no_grad()
def _predict_rows(model,data,domain,sigma,batch_size,tolerance,split,matrix_hashes):
    model.eval()
    rows=[]
    for start in range(0,len(data['rows']),batch_size):
        f=data['features'][start:start+batch_size]
        truth=data['numeric'][start:start+batch_size]
        density=model(f)
        target=base.normalized_target(truth,domain,model.bins,sigma)
        tv=(density-target).abs().sum(-1).mul(.5).mean(-1)
        kl=(target*(target.clamp_min(1e-12).log()-density.clamp_min(1e-12).log())).sum(-1).mean(-1)
        preds=base.numeric_decode(density,domain)
        proposal=model.locations(f)
        proposal=torch.stack([proposal[:,j]*(domain[j][1]-domain[j][0])+domain[j][0]
                              for j in range(2)],dim=1)
        arrays=[tensor.detach().cpu().tolist() for tensor in (preds,proposal,tv,kl)]
        for i,original in enumerate(data['rows'][start:start+batch_size]):
            rows.append(_single_row(original,arrays[0][i],arrays[1][i],
                arrays[2][i],arrays[3][i],split,
                matrix_hashes[original['matrix_index']],tolerance))
    return rows


def _case_groups(rows):
    groups=defaultdict(list)
    for r in rows:
        groups[(r['tier'],r['instance'])].append(r)
    return groups


def _group_summary(groups):
    """Every matrix/count case is counted once; all masks must pass."""
    items=list(groups.values()); count=len(items)
    def all_masks(key,group):return all(r[key] for r in group)
    if not count:return {'cases':0}
    x=sum(all_masks('X_pass',g) for g in items)
    y=sum(all_masks('Y_pass',g) for g in items)
    both=sum(all_masks('XY_pass',g) for g in items)
    center=sum(all_masks('center_XY_pass',g) for g in items)
    z=sum(all_masks('Z_pass',g) for g in items)
    transitions=Counter(
        'both_correct' if all_masks('center_XY_pass',g) and all_masks('XY_pass',g) else
        'bank_broke_correct_center' if all_masks('center_XY_pass',g) else
        'bank_rescued_incorrect_center' if all_masks('XY_pass',g) else
        'both_incorrect' for g in items)
    failure=Counter('both_pass' if all_masks('XY_pass',g) else
                    'X_only_fail' if not all_masks('X_pass',g) and all_masks('Y_pass',g) else
                    'Y_only_fail' if all_masks('X_pass',g) and not all_masks('Y_pass',g) else
                    'both_fail' for g in items)
    return {'cases':count,'X_hits':x,'Y_hits':y,'joint_hits':both,
            'X_rate':x/count,'Y_rate':y/count,'joint_rate':both/count,
            'center_joint_hits':center,'center_joint_rate':center/count,
            'Z_accuracy':z/count,'failure_classes':dict(failure),
            'center_to_density_transitions':dict(transitions)}


def _subset_summary(groups,key_fn):
    buckets=defaultdict(dict)
    for group_id,group in groups.items():
        bucket=key_fn(group[0]);buckets[bucket][group_id]=group
    return {label:_group_summary(group) for label,group in sorted(buckets.items())}


def _summarize(rows,max_examples,tolerance):
    groups=_case_groups(rows)
    by_tier=_subset_summary(groups,lambda r:r['tier'])
    by_magnitude=_subset_summary(groups,lambda r:r['magnitude_bucket'])
    by_count=_subset_summary(groups,lambda r:r['count_bucket'])
    by_root=_subset_summary(groups,lambda r:r['root_proximity'])
    by_matrix=_subset_summary(groups,lambda r:r['matrix_sha256'])
    hardest=[]
    for group in groups.values():
        if all(r['XY_pass'] for r in group):continue
        worst=max(group,key=lambda r:max(
            r['X_absolute_error']/r['X_allowed_error'] if r['X_allowed_error'] else
                (math.inf if r['X_absolute_error'] else 0),
            r['Y_absolute_error']/r['Y_allowed_error'] if r['Y_allowed_error'] else
                (math.inf if r['Y_absolute_error'] else 0)))
        score=max(worst['X_absolute_error']/worst['X_allowed_error'] if worst['X_allowed_error'] else
                    (math.inf if worst['X_absolute_error'] else 0),
                  worst['Y_absolute_error']/worst['Y_allowed_error'] if worst['Y_allowed_error'] else
                    (math.inf if worst['Y_absolute_error'] else 0))
        hardest.append((score,worst))
    hardest.sort(key=lambda item:(math.isinf(item[0]),item[0]),reverse=True)
    examples=[]
    nonzero_examples=[]
    def selected_example(score,r):
        return {'tier':r['tier'],'instance':r['instance'],'matrix_sha256':r['matrix_sha256'],
            'payoffs':[r[t] for t in ('HH','HD','DH','DD')],'hawk_count':r['hawk_count'],
            'oracle_X':r['true_X'],'oracle_Y':r['true_Y'],
            'pred_X':r['pred_X'],'pred_Y':r['pred_Y'],
            'center_X':r['center_X'],'center_Y':r['center_Y'],
            'X_absolute_error':r['X_absolute_error'],'Y_absolute_error':r['Y_absolute_error'],
            'X_allowed_error':r['X_allowed_error'],'Y_allowed_error':r['Y_allowed_error'],
            'magnitude_bucket':r['magnitude_bucket'],
            'center_joint_pass':r['center_XY_pass'],
            'excess_over_tolerance':None if math.isinf(score) else score}
    for score,r in hardest[:max_examples]:
        examples.append(selected_example(score,r))
    for score,r in hardest:
        if r['true_X'] != 0 and r['true_Y'] != 0:
            nonzero_examples.append(selected_example(score,r))
            if len(nonzero_examples)>=max_examples:break
    # Alternative error tolerances are diagnostics, not replacements for the certified gate.
    sensitivity={}
    for t in (0.01,0.02,0.05,0.10,0.20):
        successes=sum(all(_hit(r['pred_X'],r['true_X'],t) and
                          _hit(r['pred_Y'],r['true_Y'],t) for r in group)
                      for group in groups.values())
        sensitivity[f'{t:g}']={'joint_hits':successes,'joint_rate':successes/len(groups)}
    near_zero_failure_count=sum(s['cases']-s['joint_hits'] for label,s in by_magnitude.items()
                                if label in ('zero','(0,1]','(1,10]'))
    failures=len(groups)-sum(all(r['XY_pass'] for r in g) for g in groups.values())
    return {'overall':_group_summary(groups),
            'near_zero_failure_fraction':near_zero_failure_count/failures if failures else 0.,
            'near_zero_failure_count':near_zero_failure_count,
            'total_failed_cases':failures,'by_tier':by_tier,
            'by_min_oracle_magnitude':by_magnitude,
            'by_count_region':by_count,'by_nearest_oracle_root':by_root,
            'matrix_failure_leaders':[
                {'matrix_sha256':h,'cases':s['cases'],'joint_hits':s['joint_hits'],
                 'joint_rate':s['joint_rate']}
                for h,s in sorted(by_matrix.items(),key=lambda pair:(pair[1]['joint_rate'],pair[0]))[:10]],
            'mean_abs_error':{
                'X':statistics.fmean(r['X_absolute_error'] for r in rows),
                'Y':statistics.fmean(r['Y_absolute_error'] for r in rows),
                'X_center':statistics.fmean(r['X_center_absolute_error'] for r in rows),
                'Y_center':statistics.fmean(r['Y_center_absolute_error'] for r in rows),
                'X_bank_shift':statistics.fmean(abs(r['X_bank_shift']) for r in rows),
                'Y_bank_shift':statistics.fmean(abs(r['Y_bank_shift']) for r in rows)},
            'mean_density_tv':statistics.fmean(r['density_tv'] for r in rows),
            'tolerance_sensitivity':sensitivity,'hard_cases':examples,
            'hard_nonzero_cases':nonzero_examples}


def _write_csv(path,rows):
    with path.open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=CASE_FIELDS)
        writer.writeheader()
        for row in rows:writer.writerow(row)


def diagnose(args):
    if args.device=='cuda' and not torch.cuda.is_available():
        raise ValueError('BLIND_DIAG_CUDA_UNAVAILABLE')
    if args.cpu_threads<1 or args.eval_batch_size<1 or args.max_examples<0:
        raise ValueError('BLIND_DIAG_INVALID_DIMENSIONS')
    if args.device=='cpu':torch.set_num_threads(args.cpu_threads)
    splits=args.splits.split(',')
    if not splits or any(s not in ('train','dev','test') for s in splits) or len(set(splits))!=len(splits):
        raise ValueError('BLIND_DIAG_INVALID_SPLITS')
    if 'test' in splits and not args.acknowledge_test_exposure:
        raise ValueError('BLIND_DIAG_TEST_REQUIRES_EXPLICIT_ACKNOWLEDGMENT')
    if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
        raise FileExistsError('BLIND_DIAG_OUTPUT_EXISTS_NO_OVERWRITE:'+str(args.output))
    state=torch.load(args.checkpoint,map_location='cpu',weights_only=True)
    if state.get('schema')!=bm.SCHEMA:raise ValueError('BLIND_DIAG_CHECKPOINT_SCHEMA_MISMATCH')
    ident=state['identity']
    # Exact saved parameters, including seed, split hashes, and tolerance; never
    # let CLI options silently regenerate a different dataset.
    opts=argparse.Namespace(**{**ident,'device':args.device,'eval_batch_size':args.eval_batch_size,
                               'cpu_threads':args.cpu_threads,'output':None})
    bm._validate(opts)
    dataset,domain,manifest=bm._dataset(opts,torch.device(args.device))
    if bm._identity(opts,manifest,domain)!=ident:
        raise ValueError('BLIND_DIAG_DATASET_IDENTITY_MISMATCH')
    model=bm.BlindDensityNetwork(ident['banks'],ident['grid_bins'],ident['seed'],ident['hidden']).to(args.device)
    model.load_state_dict(state['model']);model.eval()
    digest=hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
    results={};csv_rows={}
    for split in splits:
        rows=_predict_rows(model,dataset[split],domain,ident['target_sigma_bins'],
                           args.eval_batch_size,ident['tolerance'],split,
                           manifest['matrix_hashes'][split])
        details=_summarize(rows,args.max_examples,ident['tolerance'])
        # Fail closed if this diagnostic ever disagrees with the certification's
        # own exact, per-mask joint gate for the SAME frozen model and dataset.
        certified=bm._gate(bm._evaluate(model,dataset[split],domain,opts),ident['min_rate'])
        for tier in bm.TIERS:
            actual=details['by_tier'][tier]['joint_rate']
            expected=certified['tiers'][tier]['joint_all_masks_instance_rate']
            if abs(actual-expected)>1e-8:
                raise AssertionError('BLIND_DIAG_GATE_MISMATCH:'+tier)
        details['certification_gate']=certified
        results[split]=details
        csv_rows[split]=rows
    report={'schema':SCHEMA,'checkpoint':str(args.checkpoint),
            'checkpoint_sha256':digest,'checkpoint_step':state['step'],
            'identity':ident,'matrix_hashes':manifest['matrix_hashes'],
            'splits':results,'tolerance':ident['tolerance'],'min_rate':ident['min_rate'],
            'oracle_labels_used_for':'read-only diagnosis only; never features or gradients',
            'prediction_source':'frozen density mean and frozen neural center proposal',
            'test_exposure_acknowledged':'test' in splits,
            'warning':'TEST case inspection makes this TEST set no longer a fresh untouched holdout.'}
    args.output.mkdir(parents=True,exist_ok=True)
    for split,rows in csv_rows.items():_write_csv(args.output/(split+'_cases.csv'),rows)
    base._atomic_json(args.output/'diagnosis.json',report)
    for split,details in results.items():
        print(f'\n{split.upper()} frozen checkpoint step={state["step"]} '
              f'banks={ident["banks"]}, 5% BOTH target={ident["min_rate"]:.0%}:')
        for tier,score in details['by_tier'].items():
            print(f'  {tier} {score["joint_rate"]:.1%} (X {score["X_rate"]:.1%}, '
                  f'Y {score["Y_rate"]:.1%}, neural-center {score["center_joint_rate"]:.1%}) '
                  f'cases={score["cases"]}')
        for bucket,score in details['by_min_oracle_magnitude'].items():
            print(f'  magnitude {bucket}: joint={score["joint_rate"]:.1%} cases={score["cases"]}')
    print('DIAGNOSIS_JSON='+str(args.output/'diagnosis.json'),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    p.add_argument('--splits',default='dev',help='train,dev or explicit test (default: dev)')
    p.add_argument('--acknowledge-test-exposure',action='store_true',
                   help='Required to inspect TEST failures; TEST is no longer a fresh holdout')
    p.add_argument('--eval-batch-size',type=int,default=256)
    p.add_argument('--cpu-threads',type=int,default=4)
    p.add_argument('--max-examples',type=int,default=30)
    p.add_argument('--output',type=Path,required=True,help='New empty directory for diagnosis.json and per-split CSVs')
    return p


def main(argv=None):return diagnose(parser().parse_args(argv))


if __name__=='__main__':main()
