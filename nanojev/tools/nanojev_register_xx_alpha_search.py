#!/usr/bin/env python3
"""Search the weakest *fully convergent* LASSO ratio for XXXXXX/YYYYYY only.

Reuses the completed v2 frozen-model captures. Begins from a verified alpha
ratio 0.01 baseline, halves the *ratio* after each wholly converged 2-register
hierarchy, then bisects the success/failure bracket. Success is defined by every
head passing the existing solver and full-design KKT tests, NOT readback rate.

The proven 0.01 baseline can be imported as a warm start from this SAME xx
capture/feature layout. Learned coordinates from the earlier bare-number
experiment, and any ab template coefficients, are never used.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

import nanojev_register_template_screen as reg

base = reg.base
fast = reg.fast
sparse = reg.sparse
DEFAULT_ROOT = reg.DEFAULT_ROOT
SEARCH_SCHEMA = 'nanojev.register-xx-alpha-search.v1'


def alpha_token(value):
    return f'{value:.12g}'.replace('.', 'p')


def read_saved_models(directory, layers, readout, columns, means, scales, features):
    """Recover selected heads and verify their exact feature coordinate basis."""
    directory = Path(directory)
    metadata_path = directory/'heads.json'
    layout_path = directory/'layout.json'
    if not metadata_path.is_file() or not layout_path.is_file():
        raise FileNotFoundError(f'XX_SAVED_MODEL_MISSING:{directory}')
    layout = json.loads(layout_path.read_text(encoding='utf-8'))
    if (list(layout['layers']) != list(layers) or layout['full_features'] != features.shape[1] or
        layout['samples'] != features.shape[0] or layout['readout_scope'] != readout['scope'] or
        (readout['scope']=='suffix' and layout['slots'] != readout['tail_tokens'])):
        raise ValueError('XX_SAVED_MODEL_FEATURE_LAYOUT_MISMATCH')
    models = {}
    for meta in json.loads(metadata_path.read_text(encoding='utf-8')):
        if not meta['solver_converged'] or not meta['kkt_passed']:
            raise ValueError(f"XX_SAVED_MODEL_NONCONVERGED_HEAD:{meta['head']}")
        with np.load(directory/meta['coefficients_file'], allow_pickle=False) as data:
            original_cols = np.asarray(data['feature_indices'], dtype=np.int32)
            positions = np.searchsorted(columns, original_cols)
            if (np.any(positions>=len(columns)) or
                not np.array_equal(columns[positions], original_cols) or
                not np.allclose(means[positions], data['feature_means'], rtol=1e-5, atol=1e-5) or
                not np.allclose(scales[positions], data['feature_scales'], rtol=1e-5, atol=1e-5)):
                raise ValueError(f"XX_SAVED_MODEL_NORMALIZATION_CHANGED:{meta['head']}")
            models[meta['head']] = {
                **meta, 'indices':positions.astype(np.int32),
                'weights':np.asarray(data['weights'],dtype=np.float64).copy(),
                'intercept':float(data['intercept']),
            }
    if len(models) < 2 or any(not any(k.startswith(f'position_{i}_') for k in models) for i in (1,2)):
        raise ValueError('XX_SAVED_MODEL_HEADS_MISSING')
    return models


def read_verified_baseline(path, root, layers, stage, readout, ratio, columns, means, scales, features):
    """Read a fully converged *same-capture* xx fit for exact warm initialization."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f'XX_BASELINE_MISSING:{path}')
    info = json.loads(path.read_text(encoding='utf-8'))
    if Path(info['capture_root']).resolve() != root.resolve():
        raise ValueError('XX_BASELINE_WRONG_CAPTURE_ROOT')
    if info.get('readout') != readout:
        raise ValueError('XX_BASELINE_READOUT_MISMATCH')
    hp = info.get('hyperparameters', {})
    if (str(hp['representation']) != 'paired' or
        int(hp['stage']) != stage or
        abs(float(hp['alpha_ratio']) - ratio) > 1e-12 or
        reg.sparse.parse_layers(str(hp['layers']), 1000) != layers):
        raise ValueError('XX_BASELINE_HYPERPARAMETER_MISMATCH')
    winner = next((r for r in info.get('rows', []) if r.get('template') == 'xx' and
                  r.get('joint_pass_rate') is not None and r.get('all_heads_converged') is True and
                  abs(float(r.get('alpha_ratio', -1)) - ratio) < 1e-12), None)
    if winner is None:
        raise ValueError('XX_BASELINE_XX_DID_NOT_CONVERGE')
    directory = path.parent / 'models' / 'xx' / f'alpha_{alpha_token(ratio)}_stage_{stage}'
    models = read_saved_models(directory, layers, readout, columns, means, scales, features)
    if any(abs(float(model['alpha_ratio']) - ratio) > 1e-12 for model in models.values()):
        raise ValueError('XX_BASELINE_ALPHA_RATIO_DRIFT')
    return winner, models


class SearchHeadFactory(reg.ProgressFastHeadFactory):
    """Retain per-head LASSO states across entire penalty search and brackets."""
    def __init__(self, *args, baseline_heads=None, position=1, columns=None, **kwargs):
        super().__init__(*args, warmup_ratios=(), **kwargs)
        self.baseline_heads = baseline_heads or {}
        self.position = position
        self.columns = columns
        self.used_baseline_heads=set()

    def fit(self, name, subset, response):
        key = fast.head_key(name, np.asarray(subset,dtype=bool),np.asarray(response,dtype=np.float64))
        if key not in self.states:
            state = fast.HeadState(self.features,np.asarray(subset,dtype=bool),np.asarray(response,dtype=np.float64))
            full_name = f'position_{self.position}_{name}'
            if full_name in self.baseline_heads:
                prior = self.baseline_heads[full_name]
                if (state.n != prior['training_samples'] or
                    not math.isclose(state.alpha_max,float(prior['alpha_max']),rel_tol=0.0001,abs_tol=1e-7)):
                    raise ValueError(f'XX_BASELINE_HEAD_TARGET_CHANGED:{full_name}')
                previous = {
                    'indices': prior['indices'], 'weights': prior['weights'],
                    'intercept':prior['intercept'], 'alpha_ratio':float(prior['alpha_ratio']),
                    'alpha':float(prior['lasso_alpha']), 'alpha_max':state.alpha_max,
                    'nonzero':int(prior['nonzero']), 'kkt_passed':True,
                    'solver_converged':True,
                }
                state.fits[previous['alpha_ratio']] = previous
                state.prior=previous
                self.used_baseline_heads.add(full_name)
            elif self.baseline_heads:
                raise ValueError(f'XX_BASELINE_HEAD_MISSING:{full_name}')
            self.states[key] = state
        # A failed weaker whole-hierarchy trial may have solved SOME heads. Do
        # not let that weaker solution replace the best valid stronger seed.
        state = self.states[key]
        stronger = [a for a in state.fits if a >= self.ratio]
        state.prior = state.fits[min(stronger)] if stronger else None
        return super().fit(name,subset,response)


def make_schedule(start, minimum, max_halvings, bisections):
    """Pure deterministic scheduling machine. send(pass/fail) after every trial.

    Halve while the *entire hierarchy* succeeds. At first failure, bisect the
    bracket linearly in alpha ratio. Stop at floor/step bounds.
    """
    if (not 0. < minimum < start <= 1. or max_halvings<1 or bisections<0):
        raise ValueError('XX_INVALID_SEARCH_BOUNDS')
    success = start
    failed = None
    for _ in range(max_halvings):
        candidate = success / 2.
        if candidate < minimum:
            break
        worked = yield candidate, 'halve', success, None
        if worked:
            success = candidate
        else:
            failed = candidate
            break
    if failed is not None:
        for _ in range(bisections):
            candidate = (success+failed) / 2.
            if candidate<=minimum or candidate==success or candidate==failed:
                break
            worked = yield candidate, 'bisect', success, failed
            if worked:
                success=candidate
            else:
                failed=candidate


def search(args):
    root = args.output_root.expanduser().resolve()
    out = (args.results_root or root/'analysis_xx_alpha_search').expanduser().resolve()
    if out == root or out.is_relative_to(root/'records'):
        raise ValueError('XX_RESULTS_MUST_NOT_OVERWRITE_CAPTURE')
    if out == Path(args.baseline_summary).expanduser().resolve().parent:
        raise ValueError('XX_RESULTS_MUST_NOT_OVERWRITE_BASELINE')
    # Reuse the EXISTING manifest; do not alter capture settings or files.
    manifest = root/'manifest.json'
    if not manifest.is_file():
        raise FileNotFoundError(f'XX_CAPTURE_MANIFEST_MISSING:{manifest}')
    settings = json.loads(manifest.read_text(encoding='utf-8'))
    if (settings.get('schema') != reg.SCHEMA or 'xx' not in settings['templates'] or
        settings['hyperparameters']['representation'] != 'paired'):
        raise ValueError('XX_CAPTURE_MUST_CONTAIN_PAIRED_XX')
    settings['readout'] = {'scope':args.readout_scope,'tail_tokens':args.tail_tokens}
    settings['hyperparameters'].update(alpha_ratio=args.start_alpha_ratio, stage=args.stage, layers=args.layers)
    reg.ensure_analysis_capture(root, settings, args)
    identity=json.loads((root/'model_identity.json').read_text(encoding='utf-8'))
    first=next(t for t in settings['prompts'].values() if t['template']=='xx')
    _,raw=reg.get_record(root,first,identity)
    layers=sparse.parse_layers(args.layers,raw.shape[0])
    stage=args.stage
    cuts=sparse.hier.REGIMES[stage-1]
    beginning=time.perf_counter()
    capture_fingerprint=hashlib.sha256(manifest.read_bytes()).hexdigest()
    status={'phase':'design_matrix','template':'xx','template_started':beginning,
            'position':None,'head':None,'head_count':0}
    stop=threading.Event()
    watcher=threading.Thread(target=reg.analysis_heartbeat,args=(status,stop,args.heartbeat_seconds),daemon=True)
    base.emit('xx_alpha_search_start', root=str(root),result_root=str(out),template='xx',
              baseline_ratio=args.start_alpha_ratio,stage=stage,layers=layers,
              ratio_floor=args.min_alpha_ratio,max_halvings=args.max_halvings,
              bisections=args.bisect_steps)
    watcher.start()
    try:
        x,pairs,operations,layout=reg.design_matrix(root,settings,'xx',layers,identity)
        base.emit('xx_alpha_search_design_ready', samples=len(pairs), features=x.shape[1],
                  seconds=round(time.perf_counter()-beginning,2))
        status['phase']='standardize'
        z,cols,means,scales=sparse.standardize_design(x)
        del x
        baseline, baseline_heads=read_verified_baseline(args.baseline_summary,root,layers,stage,
            settings['readout'],args.start_alpha_ratio,cols,means,scales,z)
        base.emit('xx_alpha_search_baseline_loaded',joint_pass_rate=baseline['joint_pass_rate'],
                  alpha_ratio=args.start_alpha_ratio,heads=len(baseline_heads),
                  coefficients=baseline['coefficient_count'])
        out.mkdir(parents=True,exist_ok=True)
        saved = out/'xx_alpha_search_summary.json'
        resume_prior = None
        search_config = {'start_alpha_ratio':args.start_alpha_ratio,
                         'min_alpha_ratio':args.min_alpha_ratio,
                         'max_halvings':args.max_halvings,'bisect_steps':args.bisect_steps,
                         'solver_max_iter':args.solver_max_iter,
                         'solver_retry_multiplier':args.solver_retry_multiplier,
                         'kkt_iters':args.kkt_iters}
        if args.resume:
            if not saved.is_file():
                raise FileNotFoundError(f'XX_RESUME_SUMMARY_MISSING:{saved}')
            resume_prior=json.loads(saved.read_text(encoding='utf-8'))
            if (resume_prior.get('schema')!=SEARCH_SCHEMA or
                resume_prior.get('capture_fingerprint')!=capture_fingerprint or
                resume_prior.get('search_config')!=search_config or
                resume_prior.get('stage')!=stage or
                resume_prior.get('layers')!=layers or
                resume_prior.get('readout')!=settings['readout'] or
                Path(resume_prior.get('baseline_summary','')).resolve()!=Path(args.baseline_summary).resolve()):
                raise ValueError('XX_RESUME_CONFIGURATION_MISMATCH')
            if resume_prior.get('search_complete'):
                base.emit('xx_alpha_search_already_complete', results_root=str(out),
                          lowest_converged=resume_prior['lowest_converged']['ratio'])
                return
            found=[r for r in resume_prior['successes'] if r['source']=='new_fit']
            if found:
                latest=min(found,key=lambda row:row['ratio'])
                directory=out/'models'/f'alpha_{alpha_token(latest["ratio"])}_stage_{stage}'
                baseline_heads=read_saved_models(directory,layers,settings['readout'],
                    cols,means,scales,z)
                base.emit('xx_alpha_search_resume_seed_loaded',ratio=latest['ratio'],
                          heads=len(baseline_heads))
        elif saved.is_file():
            raise RuntimeError('XX_EXISTING_SEARCH_RESULTS_USE_--resume_OR_NEW_--results-root')
        factories=[]
        for pos in (1,2):
            factories.append(SearchHeadFactory(z,max_iter=args.solver_max_iter,
                retry_multiplier=args.solver_retry_multiplier,kkt_iters=args.kkt_iters,
                progress=status,progress_every=args.progress_every_heads,
                position=pos,baseline_heads=baseline_heads,columns=cols))
        success=(resume_prior['successes'] if resume_prior else
                 [{'ratio':args.start_alpha_ratio,'joint_pass_rate':baseline['joint_pass_rate'],
                   'coefficient_count':baseline['coefficient_count'],'source':'verified_baseline'}])
        failures=resume_prior['failures'] if resume_prior else []
        attempts=resume_prior['attempts'] if resume_prior else []
        best=success[0]

        def checkpoint():
            sparse.dec.write_json(out/'xx_alpha_search_summary.json',{
                'schema':SEARCH_SCHEMA,'capture_root':str(root),'template':'xx',
                'capture_fingerprint':capture_fingerprint,'search_config':search_config,
                'representation':'paired','stage':stage,'layers':layers,
                'readout':settings['readout'],'baseline_summary':str(Path(args.baseline_summary).resolve()),
                'baseline':success[0],'successes':success,'failures':failures,
                'attempts':attempts,'lowest_converged':min(success,key=lambda r:r['ratio']),
                'last_failed':failures[-1] if failures else None,
                'search_complete':False,
                'interpretation':'convergence boundary, not numerical accuracy optimum; readback scored in-sample'})
            with (out/'xx_alpha_search_attempts.csv').open('w',newline='',encoding='utf-8') as f:
                if attempts:
                    w=csv.DictWriter(f,fieldnames=['ratio','phase','success','joint_pass_rate',
                        'worst_operation_joint_pass_rate','coefficient_count','seconds','failed_position','failed_head','failure_code'])
                    w.writeheader()
                    for record in attempts:w.writerow({k:record.get(k) for k in w.fieldnames})

        checkpoint()
        schedule=make_schedule(args.start_alpha_ratio,args.min_alpha_ratio,args.max_halvings,args.bisect_steps)
        try:
            proposed=next(schedule)
        except StopIteration:
            proposed=None
        if resume_prior:
            for prior_trial in attempts:
                if (proposed is None or
                    not math.isclose(float(proposed[0]),prior_trial['ratio'],rel_tol=1e-11,abs_tol=1e-14) or
                    proposed[1]!=prior_trial['phase']):
                    raise ValueError('XX_RESUME_TRIAL_SEQUENCE_MISMATCH')
                try:
                    proposed=schedule.send(bool(prior_trial['success']))
                except StopIteration:
                    proposed=None
            base.emit('xx_alpha_search_resumed',completed_trials=len(attempts),
                      pending_ratio=proposed[0] if proposed else None)
        while proposed is not None:
            ratio,phase,previous_good,previous_bad=proposed
            ratio=float(round(ratio,14))
            started=time.perf_counter()
            base.emit('xx_alpha_trial_start',alpha_ratio=ratio,phase=phase,
                      last_converged=previous_good,first_failed=previous_bad)
            status.update(phase='lasso_fit',head=None,position=None,head_count=0,
                          fitting_alpha_ratio=ratio,fitting_target_ratio=ratio)
            heads={}; predictions=np.zeros_like(pairs,dtype=np.float64)
            failed=None
            try:
                for pos,factory in enumerate(factories,1):
                    status.update(position=pos,head=None,head_count=0)
                    factory.set_ratio(ratio)
                    base.emit('xx_alpha_position_start',alpha_ratio=ratio,position=pos)
                    result=sparse.hier.fit_hierarchy(z,pairs[:,pos-1].astype(np.float64),
                                                     cuts,ratio,factory=factory)
                    predictions[:,pos-1]=result['predicted']
                    for name,head in factory.heads.items():
                        heads[f'position_{pos}_{name}']=head
                    base.emit('xx_alpha_position_complete',alpha_ratio=ratio,position=pos,
                              heads=len(factory.heads),nonzero=sum(h['nonzero'] for h in factory.heads.values()))
            except sparse.SparseFitFailure as exc:
                failed={'ratio':ratio,'phase':phase,'position':status.get('position'),
                        'head':status.get('head'),'code':exc.code,'diagnostics':exc.diagnostics}
            elapsed=round(time.perf_counter()-started,2)
            if failed is None:
                score,by_op,stable,passing,_ = reg.operation_metrics(pairs,predictions,operations)
                row={'ratio':ratio,'joint_pass_rate':score['both_within_5pct_rate'],
                     'worst_operation_joint_pass_rate':stable['worst_operation_joint_pass_rate'],
                     'all_operations_joint_pass_rate':stable['all_operations_joint_pass_rate'],
                     'coefficient_count':sum(h['nonzero'] for h in heads.values()),
                     'position_1_pass_rate':score['slot_1_pass_rate'],
                     'position_2_pass_rate':score['slot_2_pass_rate'],
                     'per_operation':by_op,'seconds':elapsed,'source':'new_fit'}
                success.append(row)
                best=min(success,key=lambda r:r['ratio'])
                model_path=out/'models'/f'alpha_{alpha_token(ratio)}_stage_{stage}'
                sparse.save_heads(model_path,heads,cols,means,scales,layout)
                sparse.dec.write_csv(out/f'predictions_alpha_{alpha_token(ratio)}.csv',[
                    {'pair_id':i//len(settings['operations']),'operation':operations[i],
                     'a':int(pairs[i,0]),'b':int(pairs[i,1]),
                     'a_hat':float(predictions[i,0]),'b_hat':float(predictions[i,1]),
                     'both_pass':bool(passing[i].all())} for i in range(len(pairs))],
                    ['pair_id','operation','a','b','a_hat','b_hat','both_pass'])
                attempts.append({'ratio':ratio,'phase':phase,'success':True,
                    'joint_pass_rate':row['joint_pass_rate'],
                    'worst_operation_joint_pass_rate':row['worst_operation_joint_pass_rate'],
                    'coefficient_count':row['coefficient_count'],'seconds':elapsed})
                base.emit('xx_alpha_trial_pass',alpha_ratio=ratio,
                    joint_pass_rate=row['joint_pass_rate'],nonzero=row['coefficient_count'],
                    worst_operation_pass_rate=row['worst_operation_joint_pass_rate'],
                    seconds=elapsed)
            else:
                failures.append(failed)
                attempts.append({'ratio':ratio,'phase':phase,'success':False,'seconds':elapsed,
                    'failed_position':failed['position'],'failed_head':failed['head'],
                    'failure_code':failed['code']})
                base.emit('xx_alpha_trial_fail',alpha_ratio=ratio,position=failed['position'],
                          head=failed['head'],failure=failed['code'],diagnostics=failed['diagnostics'],
                          seconds=elapsed)
            checkpoint()
            try:
                proposed=schedule.send(failed is None)
            except StopIteration:
                proposed=None
        summary_path=out/'xx_alpha_search_summary.json'
        final=json.loads(summary_path.read_text(encoding='utf-8'))
        final['search_complete']=True
        sparse.dec.write_json(summary_path,final)
        base.emit('xx_alpha_search_complete',lowest_converged=best['ratio'],
                  joint_pass_rate=best['joint_pass_rate'],attempts=len(attempts),
                  bracket_failed=failures[-1]['ratio'] if failures else None,
                  output_root=str(out))
    finally:
        stop.set();watcher.join(timeout=2.)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--resume',action='store_true',help='Continue from saved completed trials and models')
    p.add_argument('--output-root',type=Path,default=DEFAULT_ROOT,
                   help='Existing v2 paired-encoding capture root; read-only')
    p.add_argument('--results-root',type=Path,default=None,
                   help='Separate new results directory')
    p.add_argument('--baseline-summary',type=Path,default=None,
                   help='Converged xx 0.01 run summary and models from SAME capture/layout')
    p.add_argument('--start-alpha-ratio',type=float,default=0.01)
    p.add_argument('--min-alpha-ratio',type=float,default=0.0001)
    p.add_argument('--max-halvings',type=int,default=6)
    p.add_argument('--bisect-steps',type=int,default=5)
    p.add_argument('--stage',type=int,default=5)
    p.add_argument('--layers',default='16,28')
    p.add_argument('--readout-scope',choices=('suffix','full'),default='suffix')
    p.add_argument('--tail-tokens',type=int,default=4)
    p.add_argument('--solver-max-iter',type=int,default=10000)
    p.add_argument('--solver-retry-multiplier',type=int,default=3)
    p.add_argument('--kkt-iters',type=int,default=12)
    p.add_argument('--threads',type=int,default=8)
    p.add_argument('--heartbeat-seconds',type=float,default=30.)
    p.add_argument('--progress-every-heads',type=int,default=10)
    p.add_argument('--wait-for-capture',action='store_true')
    p.add_argument('--capture-poll-seconds',type=float,default=15.)
    p.add_argument('--normal-priority',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        trial = make_schedule(.01, .0001, 3, 2)
        assert next(trial)[0] == .005
        assert trial.send(True)[0] == .0025
        assert trial.send(False)[0] == .00375
        assert alpha_token(.01)=='0p01'
        base.emit('xx_alpha_search_self_test_passed')
        return
    if args.baseline_summary is None:
        args.baseline_summary=args.output_root/'analysis_logged_alpha_001'/'register_template_summary.json'
    if (args.threads<1 or args.stage<1 or args.stage>len(sparse.hier.REGIMES) or
        args.solver_max_iter<1 or args.solver_retry_multiplier<1 or args.kkt_iters<1 or
        args.heartbeat_seconds<=0 or args.tail_tokens<1 or args.progress_every_heads<1):
        raise ValueError('XX_INVALID_ARGUMENT')
    args.output_root=args.output_root.expanduser().resolve()
    args.baseline_summary=args.baseline_summary.expanduser().resolve()
    if not args.normal_priority:
        fast.set_background_priority()
    with threadpool_limits(limits=args.threads):
        search(args)


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError,IndexError) as exc:
        base.emit('xx_alpha_search_error',message=str(exc))
        raise SystemExit(1)
