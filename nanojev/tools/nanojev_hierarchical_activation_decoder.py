#!/usr/bin/env python3
"""Offline hierarchical decoder for captured +/-2M NanoJev numerical activations.

No model inference, holdouts, or changes to captured raw records. For all three
prompt representations and chosen layers, fit sign, zero, boundary routers,
and sign-conditioned magnitude regressors. Start with two regimes and add
predefined logarithmic magnitude bands until EVERY nonzero training probe is
within 5% of its own actual value AND zero is classified correctly. Report
actual and oracle routing separately. All fits and checks are IN-SAMPLE.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys

import numpy as np

import nanojev_decimal_number_activation_map as dec
import nanojev_multiscale_activation_decoder as base

SCHEMA = 'nanojev.hierarchical-activation-decoder.v1'
DEFAULT_SOURCE = base.DEFAULT_ROOT
DEFAULT_OUTPUT = Path.home() / 'NanoJev' / 'runs' / 'multiscale_hierarchy_5pct_v1'
# Hierarchies progress from the simplest near/far decision. These splits are
# specified before looking at fit errors, avoiding arbitrary post-hoc thresholds.
REGIMES = ((200,), (200, 20_000), (200, 20_000, 200_000),
           (200, 2_000, 20_000, 200_000),
           (20, 200, 2_000, 20_000, 200_000))
ALL_KINDS = ('decimal', 'logbar', 'paired')


def actual_band(n, boundaries):
    return int(np.searchsorted(boundaries, abs(n), side='left'))


def metrics(y, predicted, boundaries):
    y = np.asarray(y, dtype=np.float64)
    predicted = np.asarray(predicted, dtype=np.float64)
    assert y.shape == predicted.shape
    nonzero = y != 0
    rel = np.full(len(y), np.nan, dtype=np.float64)
    rel[nonzero] = np.abs(predicted[nonzero] - y[nonzero]) / np.abs(y[nonzero])
    within = np.zeros(len(y), dtype=bool)
    within[nonzero] = rel[nonzero] <= 0.05 + 1e-12
    within[~nonzero] = predicted[~nonzero] == 0
    result = {
        'count':len(y), 'nonzero_count':int(nonzero.sum()),
        'within_5pct_count':int(within[nonzero].sum()),
        'within_5pct_rate':float(np.mean(within[nonzero])) if nonzero.any() else 1.,
        'all_probes_pass':bool(np.all(within)),
        'zero_exact':bool(np.all(within[~nonzero])),
        'sign_accuracy':float(np.mean(np.sign(predicted[nonzero]) == np.sign(y[nonzero]))) if nonzero.any() else 1.,
        'mae':float(np.mean(np.abs(predicted-y))),
        'wape':float(np.sum(np.abs(predicted-y))/np.sum(np.abs(y))) if nonzero.any() else 0.,
        'max_relative_error':float(np.max(rel[nonzero])) if nonzero.any() else 0.,
        'median_relative_error':float(np.median(rel[nonzero])) if nonzero.any() else 0.,
        'p95_relative_error':float(np.percentile(rel[nonzero],95)) if nonzero.any() else 0.,
    }
    result['bands'] = []
    for b in range(len(boundaries)+1):
        mask = np.asarray([actual_band(val, boundaries)==b for val in y]) & nonzero
        if mask.any():
            result['bands'].append({'band':b, 'lower_exclusive':float(boundaries[b-1]) if b>0 else 0.,
                                    'upper_inclusive':float(boundaries[b]) if b<len(boundaries) else float(base.MAX_VALUE),
                                    'count':int(mask.sum()), 'within_5pct_rate':float(np.mean(within[mask])),
                                    'median_relative_error':float(np.median(rel[mask])),
                                    'worst_relative_error':float(np.max(rel[mask]))})
    return result, within, rel


class Projector:
    """One reusable centered affine ridge decomposition for any scalar target."""
    def __init__(self, x):
        x = np.asarray(x, dtype=np.float64)
        if x.ndim!=2 or not np.isfinite(x).all() or x.shape[0]<1:
            raise ValueError('HIERARCHY_INVALID_FEATURES')
        self.x = x
        self.xmean = x.mean(0)
        self.xc = x-self.xmean
        if len(x) <= 1:
            self.evals=np.empty(0)
            self.basis=np.empty((len(x),0))
            return
        gram = self.xc @ self.xc.T
        s, v = np.linalg.eigh(gram)
        tol=max(float(s[-1])*1e-11,1e-12)
        good=s>tol
        self.evals=s[good]
        self.basis=v[:,good]

    def fit(self, y, df):
        y=np.asarray(y,dtype=np.float64)
        if y.shape!=(len(self.x),) or not np.isfinite(y).all():
            raise ValueError('HIERARCHY_INVALID_TARGET')
        rank=len(self.evals)
        target=min(float(df), rank*.99)
        if rank==0:
            weights=np.zeros(self.x.shape[1],dtype=np.float64)
            intercept=float(np.mean(y))
            return (weights,intercept,0.,0,0.)
        low,high=0.,float(self.evals[-1])
        while float(np.sum(self.evals/(self.evals+high)))>target:
            high*=2
        for _ in range(60):
            mid=(low+high)/2
            if float(np.sum(self.evals/(self.evals+mid)))>target: low=mid
            else: high=mid
        lam=high
        coefficients=self.basis.T@(y-y.mean())
        weights=self.xc.T@(self.basis@(coefficients/(self.evals+lam)))
        intercept=float(y.mean()-self.xmean@weights)
        actual_df=float(np.sum(self.evals/(self.evals+lam)))
        return (weights,intercept,actual_df,rank,float(lam))


class HeadFactory:
    def __init__(self, features, df):
        self.features=np.asarray(features,dtype=np.float64)
        self.df=float(df)
        self.cache={}
        self.heads={}

    def fit(self, name, subset, response):
        idx=np.flatnonzero(subset)
        if not len(idx):
            raise ValueError(f'HIERARCHY_EMPTY_REGIME:{name}')
        key=tuple(idx.tolist())
        if key not in self.cache:
            self.cache[key]=Projector(self.features[idx])
        weights, intercept, effective_df, rank, lam=self.cache[key].fit(response, self.df)
        response_record={'weights':weights, 'intercept':intercept,
                         'effective_df':effective_df,'rank':rank,'ridge_lambda':lam,
                         'training_samples':len(idx)}
        self.heads[name]=response_record
        return self.features@weights+intercept


def fit_hierarchy(features, y, boundaries, df=117., factory=None):
    """Fit the head hierarchy and return BOTH actual and oracle routed outcomes."""
    features=np.asarray(features,dtype=np.float64)
    y=np.asarray(y,dtype=np.float64)
    if features.ndim!=2 or features.shape[0]!=len(y) or not np.isfinite(y).all():
        raise ValueError('HIERARCHY_BAD_SHAPES')
    if list(boundaries)!=sorted(set(boundaries)) or not boundaries or boundaries[-1]>=base.MAX_VALUE:
        raise ValueError('HIERARCHY_INVALID_BOUNDARIES')
    n=len(y)
    active=y!=0
    if factory is None:
        factory=HeadFactory(features,df)
    else:
        if factory.features.shape != features.shape or factory.df != float(df):
            raise ValueError("HIERARCHY_FACTORY_MISMATCH")
    factory.heads={}  # reuse eigendecompositions, not previous stage heads
    zero_score=factory.fit('zero',np.ones(n,dtype=bool),np.where(y==0,1.,-1.))
    # A positive score is a classified zero; unlike a lookup oracle, this can fail.
    zero_pred=zero_score>0
    sign_score=factory.fit('sign',active,np.sign(y[active]))
    sign_pred=np.where(sign_score>=0,1.,-1.)
    # Ordered binary router: each head sees only the true higher-magnitude branch.
    boundaries=list(boundaries)
    heads=[]
    for i,cutoff in enumerate(boundaries):
        lower=boundaries[i-1] if i else 0
        fit_mask=active & (np.abs(y)>lower)
        pred=factory.fit(f'route_above_{cutoff}',fit_mask,
                         np.where(np.abs(y[fit_mask])>cutoff,1.,-1.))
        heads.append(pred)
    route_pred=np.zeros(n,dtype=int)
    # Sequential decisions: evaluate next head only if earlier head said > bound.
    proceeding=np.ones(n,dtype=bool)
    for i,pred in enumerate(heads):
        passed=proceeding&(pred>0)
        route_pred[passed]=i+1
        proceeding=passed
    oracle_route=np.asarray([actual_band(v,boundaries) for v in y],dtype=int)
    y_oracle=np.zeros(n,dtype=float)
    y_predicted=np.zeros(n,dtype=float)
    expert_outputs={}
    for r in range(len(boundaries)+1):
        for sign in (-1,1):
            mask=active&(oracle_route==r)&(np.sign(y)==sign)
            if not mask.any():
                continue
            magnitudes=np.abs(y[mask]); log_transform=r!=0
            target=np.log1p(magnitudes) if log_transform else magnitudes
            key=f'expert_{r}_{"pos" if sign==1 else "neg"}'
            raw=factory.fit(key,mask,target)
            if log_transform:
                # Clip exp argument to avoid overflow for misrouted probes.
                estimated=np.expm1(np.clip(raw,-20.,math.log1p(base.MAX_VALUE)+2.))
            else:
                estimated=raw
            lower=float(boundaries[r-1]) if r else 0.
            upper=float(boundaries[r]) if r<len(boundaries) else float(base.MAX_VALUE)
            estimated=np.clip(estimated,lower,upper)
            expert_outputs[r,sign]=estimated
            use_oracle=mask
            y_oracle[use_oracle]=sign*estimated[use_oracle]
    # Ground-truth zero only in oracle routing; predicted routing uses zero head.
    for i in range(n):
        if zero_pred[i]:
            y_predicted[i]=0.
        else:
            code=(int(route_pred[i]),int(sign_pred[i]))
            if code in expert_outputs:
                y_predicted[i]=code[1]*expert_outputs[code][i]
            else:
                # No expert for empty branch: mark as zero and fail via scoring.
                y_predicted[i]=0.
    model_summary={name:{k:float(v) if isinstance(v,np.floating) else v for k,v in data.items() if k!='weights'}
                   for name,data in factory.heads.items()}
    return {'predicted':y_predicted,'oracle':y_oracle,'zero_pred':zero_pred,
            'sign_pred':sign_pred,'route_pred':route_pred,'oracle_route':oracle_route,
            'heads':factory.heads, 'head_metrics':model_summary,
            'total_effective_df':float(sum(h['effective_df'] for h in factory.heads.values()))}


def collision_feasibility(values):
    """Necessary in-sample information limit for logbar, independent of any model."""
    groups={}
    for v in values:
        groups.setdefault(base.quantize(int(v)),[]).append(int(v))
    impossible=[]
    for q, nums in groups.items():
        lower=max(min(.95*v,1.05*v) for v in nums)
        upper=min(max(.95*v,1.05*v) for v in nums)
        if lower>upper+1e-10:
            impossible.append({'code':q,'values':nums,'allowable_interval':[lower,upper]})
    return {'codes':len(groups),'collision_groups':sum(len(x)>1 for x in groups.values()),
            'infeasible_5pct_groups':len(impossible),'examples':impossible[:8]}


def write_csv(path, records):
    if not records:
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file,fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)


def run(source_root=DEFAULT_SOURCE, output_root=DEFAULT_OUTPUT, layers=(16,28), df=117.,
        kinds=ALL_KINDS, regime_steps=REGIMES):
    src=Path(source_root).expanduser().resolve()
    dst=Path(output_root).expanduser().resolve()
    if src==dst:
        raise ValueError('HIERARCHY_DESTINATION_MUST_DIFFER_FROM_SOURCE')
    if not (src/'manifest.json').is_file() or not (src/'model_identity.json').is_file():
        raise FileNotFoundError(f'HIERARCHY_MISSING_CAPTURE:{src}')
    manifest=json.loads((src/'manifest.json').read_text(encoding='utf-8'))
    identity=json.loads((src/'model_identity.json').read_text(encoding='utf-8'))
    values=manifest['values']
    precision=int(manifest['fractional_digits'])
    if sorted(values)!=list(values) or len(set(values))!=len(values):
        raise ValueError('HIERARCHY_INVALID_GRID')
    arrays=base.matrix_bank(src,values,precision,identity,list(layers))
    y=np.asarray(values,dtype=np.float64)
    dst.mkdir(parents=True,exist_ok=True)
    # Small offline captures may not populate high-magnitude branches.
    # Skip impossible boundaries and identical stages; full 507-grid retains all.
    evaluated_steps=[]
    for cuts in regime_steps:
        usable=tuple(t for t in cuts if np.any(np.abs(y)>t))
        if usable and usable not in evaluated_steps:
            evaluated_steps.append(usable)
    if not evaluated_steps:
        raise ValueError('HIERARCHY_NO_NONEMPTY_MAGNITUDE_SPLIT')
    rows=[]; metrics_out=[]; head_rows=[]; fitted={}; outcomes={}; baselines={}
    for layer_idx,layer in enumerate(layers):
        for kind in kinds:
            x=arrays[kind][:,layer_idx,:]
            factory=HeadFactory(x,df)
            global_prediction=factory.fit('global',np.ones(len(y),dtype=bool),y)
            baseline_result,_,_=metrics(y,global_prediction,evaluated_steps[0])
            baselines[f'{kind}_layer_{layer}']=baseline_result
            winner=None
            for stage,boundaries in enumerate(evaluated_steps,1):
                run_fit=fit_hierarchy(x,y,boundaries,df,factory=factory)
                actual,passing,relative=metrics(y,run_fit['predicted'],boundaries)
                oracle,oracle_passing,_=metrics(y,run_fit['oracle'],boundaries)
                route_correct=float(np.mean((run_fit['route_pred']==run_fit['oracle_route'])[y!=0]))
                head_prefix=f'{kind}_layer_{layer}_stage_{stage}'
                outcome={'layer':int(layer),'representation':kind,'stage':stage,
                         'regime_count':len(boundaries)+1,'boundaries':list(boundaries),
                         'effective_df_per_head_requested':df,
                         'total_effective_df':run_fit['total_effective_df'],
                         'routing_accuracy_nonzero':route_correct,
                         'zero_detector_correct':bool(np.all(run_fit['zero_pred'][y==0]) and
                                                      not np.any(run_fit['zero_pred'][y!=0])),
                         'predicted':actual,'oracle':oracle}
                metrics_out.append(outcome)
                for name,h in run_fit['heads'].items():
                    fitted[f'{head_prefix}_{name}_weights']=h['weights']
                    fitted[f'{head_prefix}_{name}_intercept']=np.float64(h['intercept'])
                    head_rows.append({'layer':layer,'representation':kind,'stage':stage,
                                      'head':name,**run_fit['head_metrics'][name]})
                for idx,n in enumerate(values):
                    rows.append({'layer':layer,'representation':kind,'stage':stage,
                                 'regimes':len(boundaries)+1,'number':n,
                                 'true_sign':int(np.sign(n)),
                                 'predicted_sign':int(run_fit['sign_pred'][idx]) if not run_fit['zero_pred'][idx] else 0,
                                 'true_regime':int(run_fit['oracle_route'][idx]),
                                 'predicted_regime':int(run_fit['route_pred'][idx]),
                                 'predicted_number':float(run_fit['predicted'][idx]),
                                 'oracle_number':float(run_fit['oracle'][idx]),
                                 'relative_error':float(relative[idx]) if n else '',
                                 'within_5pct':bool(passing[idx]),
                                 'oracle_within_5pct':bool(oracle_passing[idx])})
                print(json.dumps({'event':'hierarchy_stage','layer':layer,'representation':kind,
                                  'regimes':len(boundaries)+1,'actual_pass_rate':actual['within_5pct_rate'],
                                  'oracle_pass_rate':oracle['within_5pct_rate'],
                                  'sign_accuracy':actual['sign_accuracy'],
                                  'zero_exact':actual['zero_exact'],
                                  'total_effective_df':run_fit['total_effective_df']},sort_keys=True),flush=True)
                winner=outcome
                if actual['all_probes_pass']:
                    break
            outcomes[f'{kind}_layer_{layer}']=winner
    result={'schema':SCHEMA,'scope':'IN_SAMPLE: identical captures used to fit and score',
            'source_root':str(src),'numbers':len(values),'layers':list(layers),
            'representations':list(kinds),'requested_effective_df_per_head':df,
            'success':'zero exact AND every nonzero x has abs(predicted-x)/abs(x) <= .05',
            'zero_policy':'zero must be predicted exactly as zero by zero detector',
            'logbar_feasibility':collision_feasibility(values),
            'baseline_by_representation_layer':baselines,
            'stages':metrics_out,'final_by_representation_layer':outcomes}
    write_csv(dst/'hierarchy_predictions.csv',rows)
    write_csv(dst/'hierarchy_heads.csv',head_rows)
    np.savez_compressed(dst/'hierarchy_models.npz',**fitted)
    dec.write_json(dst/'hierarchy_summary.json',result)
    print(json.dumps({'event':'hierarchy_complete','output_root':str(dst),
                      'numbers':len(values),'conclusions':{
                       key:{'stage':val['stage'],'regimes':val['regime_count'],
                            'within_5pct_rate':val['predicted']['within_5pct_rate'],
                            'all_probes_pass':val['predicted']['all_probes_pass'],
                            'oracle_within_5pct_rate':val['oracle']['within_5pct_rate']}
                       for key,val in outcomes.items()}},sort_keys=True),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,default=DEFAULT_SOURCE)
    p.add_argument('--output-root',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--effective-df',type=float,default=117.)
    p.add_argument('--layers',default='16,28')
    p.add_argument('--representations',default='decimal,logbar,paired')
    p.add_argument('--self-test',action='store_true')
    args=p.parse_args(argv)
    if args.self_test:
        assert len(base.grid())==507
        assert actual_band(200,(200,))==0 and actual_band(201,(200,))==1
        assert len(REGIMES)==5
        print(json.dumps({'event':'hierarchy_self_test_passed'}))
        return
    if not math.isfinite(args.effective_df) or args.effective_df<=0:
        raise ValueError('HIERARCHY_BAD_DF')
    layers=tuple(sorted({int(v) for v in args.layers.split(',') if v.strip()}))
    kinds=tuple(v.strip() for v in args.representations.split(',') if v.strip())
    if not layers or any(v<0 for v in layers) or not kinds or any(k not in ALL_KINDS for k in kinds):
        raise ValueError('HIERARCHY_BAD_ARGUMENTS')
    run(args.source_root,args.output_root,layers,args.effective_df,kinds)


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as e:
        print(json.dumps({'event':'hierarchy_error','message':str(e)}),file=sys.stderr)
        raise SystemExit(1)
