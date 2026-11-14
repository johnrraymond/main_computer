#!/usr/bin/env python3
"""Fast *independent* two-number full-token LASSO analysis, no model retraining.

Additive companion to nanojev_two_number_sparse_decoder.py; reads the SAME
manifest and full-token records, never changes their contents, never pools.
Identical L1 objective / per-head alpha rule / hierarchy / five-percent metric.

Optimizations: cache identical heads across hierarchy stages; warm-start each
head along decreasing alpha ratios and on active-set expansion; cache alpha_max
and starting correlations per head; checkpoint per penalty/representation.
A new script with a separate result directory; original runner untouched.
The achieved runtime improvement is data/hardware dependent: benchmark it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import warnings
from pathlib import Path

from threadpoolctl import threadpool_limits

import numpy as np
from sklearn.linear_model import Lasso
from sklearn.exceptions import ConvergenceWarning

import nanojev_two_number_sparse_decoder as base

sparse = base.sparse
SCHEMA = 'nanojev.two-number-fast-lasso.v2'
DEFAULT_SOURCE_ROOT = base.DEFAULT_ROOT
DEFAULT_FAST_OUTPUT = base.DEFAULT_ROOT.parent / 'two_number_sparse_fast_v1'


def head_key(name, mask, response):
    """Actual row membership and target distinguish superficially similar heads."""
    indices = np.flatnonzero(mask).astype(np.int32, copy=False)
    target = np.ascontiguousarray(response, dtype=np.float64)
    h = hashlib.sha256()
    h.update(indices.tobytes())
    h.update(target.tobytes())
    return name, h.hexdigest()


class HeadState:
    """One fixed design subset and target, reusable through penalty path."""
    def __init__(self, features, mask, target):
        self.rows = np.flatnonzero(mask)
        self.features = features  # Do NOT copy the full dataset.
        self.y = np.asarray(target, dtype=np.float64).copy()
        self.n = len(self.rows)
        self.p = features.shape[1]
        self.ym = float(self.y.mean())
        self.spread = float(self.y.std())
        self.yn = ((self.y - self.ym) / self.spread) if self.spread >= 1e-12 else None
        self.alpha_max = 0.
        self.order = None
        self.fits = {}           # alpha ratio -> output, exact stage reuse
        self.prior = None        # last solved head at a stronger penalty
        self.solve_calls = 0
        self.cache_hits = 0
        self.warm_starts = 0
        if self.yn is not None:
            # Compute alpha_max/order ONCE per unique head across alpha ratios.
            x = features[self.rows]
            xmean = x.mean(axis=0, dtype=np.float64)
            cross = (x.T @ self.yn) / self.n - xmean * float(self.yn.mean())
            self.order = np.argsort(-np.abs(cross), kind='stable')
            self.alpha_max = float(np.max(np.abs(cross)))
            if self.alpha_max <= 1e-12:
                raise sparse.SparseFitFailure('SPARSE_HEAD_NO_CORRELATIONS',samples=self.n,features=self.p)

    def solve(self, ratio, max_iter=10000, retry_multiplier=3, kkt_iters=12,
              kkt_tol=0.02):
        ratio = float(ratio)
        if ratio in self.fits:
            self.cache_hits += 1
            return self.fits[ratio]
        if self.yn is None:
            result = {'indices':np.empty(0,dtype=np.int32),'weights':np.empty(0),
                      'intercept':self.ym,'alpha':0.,'alpha_max':0.,'alpha_ratio':ratio,
                      'nonzero':0,'kkt_passed':True,'kkt_violation':0.,
                      'training_samples':self.n,'solver_converged':True,
                      'solver_warning_count':0,'solver_warning_fits':0,
                      'solver_fit_calls':0,'solver_last_iterations':0,
                      'solver_last_dual_gap':0.}
            self.fits[ratio] = result
            return result
        x = self.features[self.rows]  # current head's true, unaveraged columns
        alpha = ratio * self.alpha_max
        # Warm-start must go from stronger to weaker penalty only.
        prior = self.prior if self.prior and self.prior['alpha_ratio'] >= ratio else None
        top = self.order[:min(self.p,128)].astype(np.int32)
        active = np.sort(np.unique(np.concatenate((top, prior['indices'])))) if prior is not None else np.sort(top)
        old_active = None
        old_coef = None
        old_intercept = 0.
        if prior is not None:
            old_active = prior['indices']
            old_coef = prior['weights'] / self.spread
            old_intercept = (prior['intercept'] - self.ym) / self.spread
            self.warm_starts += 1
        fit_calls=warn_count=warn_fits=0
        last_niter=0; last_gap=math.inf; violation=math.inf
        for iteration in range(kkt_iters):
            # Build warm initial coefficients in CURRENT active coordinates.
            coef = np.zeros(len(active), dtype=np.float64)
            if old_active is not None and len(old_active):
                pos = np.searchsorted(active, old_active)
                if np.any(pos>=len(active)) or np.any(active[pos]!=old_active):
                    raise RuntimeError('FAST_LASSO_BAD_ACTIVE_SET_MAPPING')
                coef[pos] = old_coef
            reg = Lasso(alpha=alpha,fit_intercept=True,selection='cyclic',
                        tol=1e-5,max_iter=max_iter,warm_start=True)
            reg.coef_=coef
            reg.intercept_=float(old_intercept)
            converged=False
            xs=np.asfortranarray(x[:,active],dtype=np.float32)
            for attempt, cap in enumerate((max_iter,max_iter*retry_multiplier)):
                if attempt and cap==max_iter: break
                reg.set_params(max_iter=cap)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always',ConvergenceWarning)
                    reg.fit(xs,self.yn)
                nw=sum(issubclass(w.category,ConvergenceWarning) for w in caught)
                fit_calls+=1; warn_count+=nw; warn_fits+=int(nw>0)
                last_niter=int(reg.n_iter_)
                last_gap=float(reg.dual_gap_)
                converged=(nw==0 and math.isfinite(last_gap))
                if converged: break
            if not converged:
                raise sparse.SparseFitFailure('SPARSE_HEAD_SOLVER_NOT_CONVERGED',
                    samples=self.n,features=self.p,active_features=len(active),
                    alpha_ratio=ratio,alpha=alpha,kkt_iteration=iteration+1,
                    solver_fit_calls=fit_calls,solver_warning_count=warn_count,
                    solver_last_dual_gap=last_gap)
            residue = self.yn-reg.predict(xs)
            gradient = (x.T@residue)/self.n  # full feature KKT test
            outside=np.ones(self.p,dtype=bool)
            outside[active]=False
            violation=float(np.max(np.maximum(0.,np.abs(gradient[outside])-alpha))) if outside.any() else 0.
            if violation <= alpha*kkt_tol+1e-10:
                selected=np.flatnonzero(np.abs(reg.coef_)>1e-7)
                result={'indices':active[selected].astype(np.int32),
                        'weights':reg.coef_[selected].astype(np.float64)*self.spread,
                        'intercept':float(reg.intercept_*self.spread+self.ym),
                        'alpha':alpha,'alpha_max':self.alpha_max,'alpha_ratio':ratio,
                        'nonzero':int(len(selected)),'kkt_passed':True,
                        'kkt_violation':violation,'training_samples':self.n,
                        'solver_converged':True,'solver_warning_count':int(warn_count),
                        'solver_warning_fits':int(warn_fits),
                        'solver_fit_calls':int(fit_calls),
                        'solver_last_iterations':last_niter,
                        'solver_last_dual_gap':last_gap}
                self.fits[ratio]=result
                self.prior=result
                self.solve_calls+=1
                return result
            added=np.flatnonzero(outside & (np.abs(gradient)>alpha*(1+kkt_tol)))
            if not len(added):
                raise sparse.SparseFitFailure('SPARSE_HEAD_KKT_NOT_CONVERGED',
                    samples=self.n,features=self.p,active_features=len(active),
                    alpha_ratio=ratio,alpha=alpha,kkt_violation=violation,
                    kkt_iteration=iteration+1)
            added=added[np.argsort(-np.abs(gradient[added]),kind='stable')[:max(128,len(active))]]
            old_active=active
            old_coef=np.asarray(reg.coef_,dtype=np.float64).copy()
            old_intercept=float(reg.intercept_)
            active=np.sort(np.unique(np.concatenate((active,added))))
        raise sparse.SparseFitFailure('SPARSE_HEAD_KKT_NOT_CONVERGED',
            samples=self.n,features=self.p,active_features=len(active),
            alpha_ratio=ratio,alpha=alpha,kkt_iterations=kkt_iters,
            kkt_violation=violation,solver_fit_calls=fit_calls)


class FastHeadFactory:
    """Hierarchy-compatible factory with exact cache and penalty continuation."""
    def __init__(self,features,max_iter=10000,retry_multiplier=3,kkt_iters=12):
        self.features=np.asarray(features,dtype=np.float32)
        self.df=1.
        self.ratio=1.
        self.max_iter=max_iter
        self.retry_multiplier=retry_multiplier
        self.kkt_iters=kkt_iters
        self.states={}
        self.heads={}
        self.cache_hits=0
        self.unique_solves=0

    def set_ratio(self,ratio):
        self.ratio=self.df=float(ratio)

    def fit(self,name,subset,response):
        mask=np.asarray(subset,dtype=bool)
        if not mask.any():
            raise ValueError('SPARSE_HEAD_EMPTY_SUBSET')
        response=np.asarray(response,dtype=np.float64)
        key=head_key(name,mask,response)
        if key not in self.states:
            try:
                self.states[key]=HeadState(self.features,mask,response)
            except sparse.SparseFitFailure as exc:
                exc.diagnostics['head']=name
                raise
        state=self.states[key]
        cached=self.ratio in state.fits
        try:
            head=state.solve(self.ratio,max_iter=self.max_iter,
                             retry_multiplier=self.retry_multiplier,kkt_iters=self.kkt_iters)
        except sparse.SparseFitFailure as exc:
            exc.diagnostics['head']=name
            raise
        self.cache_hits += int(cached)
        self.unique_solves += int(not cached)
        self.heads[name]={**head,'effective_df':float(head['nonzero']),
                          'rank':min(int(mask.sum())-1,int(self.features.shape[1])),
                          'lasso_alpha':head['alpha']}
        return self.features[:,head['indices']]@head['weights']+head['intercept']


def separate_roots(source_root, output_root):
    """Never allow results to be written into or around the original run tree."""
    source = Path(source_root).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    if source == output or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('FAST_CONCURRENT_SOURCE_OUTPUT_OVERLAP')
    return source, output


def set_background_priority():
    """Reduce Windows scheduling priority of ONLY this Python process."""
    if sys.platform != 'win32':
        return False
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.SetPriorityClass.restype = ctypes.c_int
    # BELOW_NORMAL_PRIORITY_CLASS: lower priority than the original slow run.
    if not kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000):
        raise OSError(ctypes.get_last_error(), 'FAST_CONCURRENT_PRIORITY_FAILED')
    return True


def analyze_fast(args):
    source, output = separate_roots(args.source_root, args.output_root)
    if not (source/'manifest.json').is_file() or not (source/'model_identity.json').is_file():
        raise FileNotFoundError(f'FAST_CONCURRENT_SOURCE_INCOMPLETE:{source}')
    output.mkdir(parents=True, exist_ok=True)
    manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
    identity=json.loads((source/'model_identity.json').read_text(encoding='utf-8'))
    pairs=np.asarray(manifest['pairs'],dtype=np.int64)
    kinds=tuple(k.strip() for k in args.representations.split(',') if k.strip())
    ratios=tuple(float(x) for x in args.alpha_ratios.split(',') if x.strip())
    if len(set(ratios)) != len(ratios) or any(not 0<r<=1 for r in ratios):
        raise ValueError('FAST_LASSO_BAD_PENALTY_PATH')
    # Descending ratio order is required for warm-start continuation.
    if any(a<b for a,b in zip(ratios,ratios[1:])):
        raise ValueError('FAST_LASSO_PENALTIES_MUST_BE_DESCENDING')
    if not kinds or any(k not in manifest['representations'] for k in kinds):
        raise ValueError('FAST_LASSO_BAD_REPRESENTATIONS')
    first=kinds[0]
    _,tensor=base.read_record(source,first,manifest['task_digests'][first][0],
                              base.prompt(first,pairs[0]),identity)
    layers=sparse.parse_layers(args.layers,tensor.shape[0])
    result_rows=[]; errors=[]; predictions=[]
    def checkpoint():
        if result_rows:
            sparse.dec.write_csv(output/'fast_two_number_fit_curve.csv',result_rows,list(result_rows[0]))
        if predictions:
            sparse.dec.write_csv(output/'fast_two_number_predictions.csv',predictions,list(predictions[0]))
        sparse.dec.write_json(output/'fast_two_number_fit_failures.json',errors)
        sparse.dec.write_json(output/'fast_two_number_summary.json',{
            'schema':SCHEMA,'in_sample_only':True,'source_records_unchanged':True,
            'source_root':str(source),'results_root':str(output),
            'no_token_pooling':True,'layers':layers,'alpha_ratios':list(ratios),
            'rows':result_rows,'failures':errors,'status':'partial' if errors else 'valid_results_only'})
    for kind in kinds:
        x,layout=base.matrix(source,kind,manifest,layers,identity)
        z,cols,means,scales=sparse.standardize_design(x)
        del x
        factories=[FastHeadFactory(z,max_iter=args.solver_max_iter,
                    retry_multiplier=args.solver_retry_multiplier,kkt_iters=args.kkt_iters) for _ in range(2)]
        base.emit('fast_two_number_design_ready',representation=kind,
                  samples=len(pairs),full_features=layout['full_features'],
                  nonconstant_features=z.shape[1],token_slots=layout['slots'])
        for ratio in ratios:
            started=time.perf_counter()
            for factory in factories: factory.set_ratio(ratio)
            winner=None; problem=None
            before_hits=sum(f.cache_hits for f in factories)
            before_solves=sum(f.unique_solves for f in factories)
            for stage,cuts in enumerate(sparse.hier.REGIMES[:args.max_regimes-1],1):
                if not np.any(np.abs(pairs)>cuts[-1]): continue
                result_predictions=np.zeros((len(pairs),2),dtype=np.float64)
                result_oracle=np.zeros_like(result_predictions)
                saved={}
                try:
                    for slot,factory in enumerate(factories):
                        fitted=sparse.hier.fit_hierarchy(z,pairs[:,slot].astype(np.float64),
                                                         cuts,float(ratio),factory=factory)
                        result_predictions[:,slot]=fitted['predicted']
                        result_oracle[:,slot]=fitted['oracle']
                        for name,head in factory.heads.items():
                            saved[f'position_{slot+1}_{name}']=head
                except sparse.SparseFitFailure as exc:
                    problem={'representation':kind,'alpha_ratio':ratio,'stage':stage,
                             'code':exc.code,'details':exc.diagnostics}
                    errors.append(problem)
                    break
                score,passing,rel=base.joint_metrics(pairs,result_predictions)
                oracle,_,_=base.joint_metrics(pairs,result_oracle)
                result={'representation':kind,'alpha_ratio':ratio,'stage':stage,
                        'regimes':len(cuts)+1,'boundaries':list(cuts),
                        'pair_count':len(pairs),'joint_pass_rate':score['both_within_5pct_rate'],
                        'joint_pass_count':score['both_within_5pct_count'],
                        'all_pairs_pass':score['all_pairs_pass'],
                        'slot_1_pass_rate':score['slot_1_pass_rate'],
                        'slot_2_pass_rate':score['slot_2_pass_rate'],
                        'oracle_joint_pass_rate':oracle['both_within_5pct_rate'],
                        'both_sign_accuracy':score['both_sign_accuracy'],
                        'zero_exact':score['zero_exact'],
                        'extreme_ratio_1000x_count':score['extreme_ratio_1000x_count'],
                        'extreme_ratio_1000x_joint_pass_rate':score['extreme_ratio_1000x_joint_pass_rate'],
                        'max_relative_error':score['max_relative_error'],
                        'reversed_pair_within_5pct_consistency':score['reversed_pair_within_5pct_consistency'],
                        'coefficient_count':int(sum(h['nonzero'] for h in saved.values())),
                        'fitted_heads':len(saved),
                        'solver_warning_count':int(sum(h['solver_warning_count'] for h in saved.values())),
                        'all_heads_converged':all(h['solver_converged'] and h['kkt_passed'] for h in saved.values())}
                result_rows.append(result)
                winner=(result,result_predictions,result_oracle,passing,rel,saved)
                if score['all_pairs_pass']: break
            if winner is not None:
                result,est,oracle,passing,rel,heads=winner
                token=f'{ratio:.8g}'.replace('.','p')
                target=output/'fast_two_number_models'/kind/f'alpha_{token}_stage_{result["stage"]}'
                sparse.save_heads(target,heads,cols,means,scales,layout)
                for i,(pair,pred,oracle_pred) in enumerate(zip(pairs,est,oracle)):
                    predictions.append({'representation':kind,'alpha_ratio':ratio,
                         'stage':result['stage'],'pair_id':i,
                         'x1':int(pair[0]),'x2':int(pair[1]),
                         'x1_hat':float(pred[0]),'x2_hat':float(pred[1]),
                         'x1_oracle':float(oracle_pred[0]),'x2_oracle':float(oracle_pred[1]),
                         'x1_relative_error':float(rel[i,0]) if pair[0]!=0 else '',
                         'x2_relative_error':float(rel[i,1]) if pair[1]!=0 else '',
                         'x1_pass':bool(passing[i,0]),'x2_pass':bool(passing[i,1]),
                         'both_pass':bool(passing[i].all())})
            checkpoint()
            base.emit('fast_two_number_penalty_progress',representation=kind,
                 alpha_ratio=ratio,best_joint_pass_rate=winner[0]['joint_pass_rate'] if winner else None,
                 actual_nonzero_coefficients=winner[0]['coefficient_count'] if winner else None,
                 stage=winner[0]['stage'] if winner else None,
                 cache_hits=sum(f.cache_hits for f in factories)-before_hits,
                 new_head_solves=sum(f.unique_solves for f in factories)-before_solves,
                 seconds=round(time.perf_counter()-started,3),
                 failure=problem['code'] if problem else None)
        del z
    checkpoint()
    base.emit('fast_two_number_complete',source_root=str(source),results_root=str(output),
              pairs=len(pairs),valid_stages=len(result_rows),
              failed_stages=len(errors),winners=[r for r in result_rows if r['all_pairs_pass']])
    return result_rows


def parser():
    p=base.parser()
    p.description=__doc__
    p.add_argument('--source-root',type=Path,default=DEFAULT_SOURCE_ROOT,
        help='Read-only original capture directory (manifest, identity and records).')
    p.set_defaults(output_root=DEFAULT_FAST_OUTPUT, analyze_only=True)
    p.add_argument('--threads',type=int,default=2,
        help='Maximum BLAS/OpenMP threads used by the fast process (default: 2).')
    p.add_argument('--normal-priority',action='store_true',
        help='Do not lower Windows priority of the fast process.')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        assert len(base.generate_pairs(2000))==2000
        base.emit('fast_two_number_self_test_passed')
        return
    if args.threads < 1:
        raise ValueError('FAST_CONCURRENT_BAD_THREADS')
    if not args.analyze_only or args.capture_only or args.prepare_only or args.resume:
        raise ValueError('FAST_CONCURRENT_ANALYZE_ONLY')
    separate_roots(args.source_root, args.output_root)
    if args.max_regimes<2 or args.max_regimes>len(sparse.hier.REGIMES)+1:
        raise ValueError('FAST_TWO_NUMBER_BAD_REGIMES')
    if args.solver_max_iter<1 or args.kkt_iters<1 or args.solver_retry_multiplier<1:
        raise ValueError('FAST_TWO_NUMBER_BAD_SOLVER_SETTINGS')
    background = False if args.normal_priority else set_background_priority()
    base.emit('fast_two_number_concurrent_start',source_root=str(Path(args.source_root).resolve()),
              results_root=str(Path(args.output_root).resolve()),threads=args.threads,
              below_normal_priority=background)
    # LASSO is single-core coordinate descent; limit threaded BLAS used by full
    # activation gradients so it does not monopolize the long run's CPU.
    with threadpool_limits(limits=args.threads):
        analyze_fast(args)


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as exc:
        base.emit('fast_two_number_error',message=str(exc))
        raise SystemExit(1)
