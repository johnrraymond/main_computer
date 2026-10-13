#!/usr/bin/env python3
"""Capture *all* prompt-token hidden states, then fit independent sparse L1 hierarchies.

No token averaging, no PCA, no reuse of learned weights between prompt kinds.
The frozen Qwen model supplies tensors [hidden_state_layer, token, neuron].
Right-aligned, zero-padded token slots form a positional design matrix; constant
columns may be omitted because they contain no discriminating information.
Supervised active-set LASSO chooses nonzero coordinates across tokens/layers.
The same recorded 507 probes are used for fitting and scoring: IN SAMPLE ONLY.

Requires nanojev_multiscale_activation_decoder.py,
nanojev_hierarchical_activation_decoder.py, and their previous dependencies.
Requires numpy, scipy and scikit-learn for decoding, torch for new capture.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import warnings

import numpy as np

import nanojev_decimal_number_activation_map as dec
import nanojev_multiscale_activation_decoder as multi
import nanojev_hierarchical_activation_decoder as hier
import nanojev_numerate_spine_cassette_smoke as cassette

SCHEMA = 'nanojev.full-token-sparse-hierarchy.v1'  # capture manifest stays compatible
ROOT = Path.home() / 'NanoJev' / 'runs' / 'full_token_sparse_hierarchy_v1'
KINDS = ('decimal', 'logbar', 'paired')
DEFAULT_ALPHA_RATIOS = (1.0, 0.70, 0.50, 0.35, 0.25, 0.18, 0.12, 0.08, 0.05, 0.03, 0.015, 0.005)


def emit(event, **data):
    print(json.dumps({'event':event, **data}, sort_keys=True, default=str), flush=True)


def full_record_path(root, kind, key):
    return root / 'records' / f'{kind}_{key:+09d}.npz'


def signature(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def read_full(root, kind, key, expected_prompt, identity):
    path = full_record_path(root, kind, key)
    if not path.is_file():
        raise FileNotFoundError(f'FULL_ACTIVATION_RECORD_MISSING:{path}')
    with np.load(path, allow_pickle=False) as f:
        meta = json.loads(str(f['metadata'].item()))
        tensor = np.asarray(f['hidden_states'])
    if (meta.get('prompt') != expected_prompt or
            meta.get('sha256') != dec.digest(expected_prompt) or
            meta.get('identity') != identity or
            meta.get('shape') != list(tensor.shape) or
            meta.get('input_token_count') != len(meta.get('input_ids', [])) or
            tensor.ndim != 3 or tensor.shape[1] != meta['input_token_count'] or
            not np.isfinite(tensor).all()):
        raise RuntimeError(f'FULL_ACTIVATION_RECORD_DRIFT:{path}')
    return meta, tensor


def capture_full(torch, model, tokenizer, text, device, limit, storage_dtype):
    encoded = tokenizer(text, add_special_tokens=False)
    ids = encoded['input_ids']
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    if not ids or len(ids) > limit:
        raise RuntimeError(f'FULL_ACTIVATION_PROMPT_TOKENS:{len(ids)}:{limit}')
    inputs = torch.tensor([ids], dtype=torch.long, device=device)
    with torch.inference_mode():
        result = model(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                       output_hidden_states=True, use_cache=False)
        if not result.hidden_states:
            raise RuntimeError('FULL_ACTIVATION_MODEL_DID_NOT_RETURN_STATES')
        target_dtype = torch.float16 if storage_dtype == 'float16' else torch.float32
        states = torch.stack([h[0].to(device='cpu', dtype=target_dtype)
                              for h in result.hidden_states], dim=0).numpy()
    if states.ndim != 3 or states.shape[1] != len(ids) or not np.isfinite(states).all():
        raise RuntimeError('FULL_ACTIVATION_BAD_SHAPE')
    return {'prompt':text, 'sha256':dec.digest(text), 'input_ids':list(map(int,ids)),
            'input_token_count':len(ids), 'shape':list(states.shape)}, states


def prepare_plan(args):
    values = multi.grid(args.limit)
    tasks, groups = multi.task_bank(values, 1)
    settings = {'schema':SCHEMA, 'source':cassette.SOURCE[args.model][0],
                'revision':cassette.SOURCE[args.model][1], 'model':args.model,
                'values':values, 'fractional_digits':1,
                'storage_dtype':args.store_dtype,
                'max_prompt_tokens':args.max_prompt_tokens,
                'templates':{'decimal':dec.PREFIX+'{n}',
                             'logbar':dec.PREFIX+'{encoded_log}',
                             'paired':dec.PREFIX+'{n}, {encoded_log}'}}
    return values, tasks, groups, settings


def capture(args):
    values, tasks, groups, settings = prepare_plan(args)
    root = args.output_root.expanduser().resolve()
    manifest = root / 'manifest.json'
    if args.prepare_only:
        emit('full_activation_plan', numbers=len(values), tasks=len(tasks),
             unique_logbar=len(groups), record_dtype=args.store_dtype,
             max_tokens=args.max_prompt_tokens, examples=[multi.prompt('paired',n) for n in (-2_000_000,0,2_000_000)])
        return
    if manifest.is_file():
        old = json.loads(manifest.read_text(encoding='utf-8'))
        if old != settings:
            raise RuntimeError('FULL_ACTIVATION_MANIFEST_DRIFT')
        if not args.resume and not args.analyze_only:
            raise RuntimeError('FULL_ACTIVATION_EXISTING_RUN_USE_RESUME')
    elif args.analyze_only:
        raise FileNotFoundError(f'FULL_ACTIVATION_MANIFEST_MISSING:{manifest}')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('FULL_ACTIVATION_OUTPUT_NOT_EMPTY')
    if args.analyze_only:
        return
    identity_path = root / 'model_identity.json'
    existing_identity = json.loads(identity_path.read_text(encoding='utf-8')) if identity_path.is_file() else None
    all_exist = manifest.is_file() and existing_identity is not None and all(
        full_record_path(root, k, key).is_file() for k,key,_ in tasks)
    if all_exist:
        for k,key,text in tasks:
            read_full(root, k, key, text, existing_identity)
        emit('full_activation_capture_already_complete', tasks=len(tasks), root=str(root))
        return
    torch, model, tokenizer, device, info = cassette.load_source(args.model, args)
    try:
        identity = {'model':args.model, 'model_info':info}
        if existing_identity is not None and identity != existing_identity:
            raise RuntimeError('FULL_ACTIVATION_MODEL_IDENTITY_DRIFT')
        (root / 'records').mkdir(parents=True, exist_ok=True)
        if not manifest.is_file():
            dec.write_json(manifest, settings)
        if not identity_path.is_file():
            dec.write_json(identity_path, identity)
        for i, (kind, key, prompt) in enumerate(tasks, 1):
            path = full_record_path(root, kind, key)
            if path.is_file():
                if not args.resume:
                    raise RuntimeError(f'FULL_ACTIVATION_DUPLICATE:{path}')
                read_full(root,kind,key,prompt,identity)
            else:
                metadata, state = capture_full(torch, model, tokenizer, prompt, device,
                                               args.max_prompt_tokens, args.store_dtype)
                metadata['identity'] = identity
                # Write then rename; interrupted captures never leave a valid record.
                tmp = path.with_name(path.name + '.partial')
                with tmp.open('wb') as fh:
                    np.savez_compressed(fh, metadata=json.dumps(metadata,sort_keys=True),
                                        hidden_states=state)
                tmp.replace(path)
            if i%args.report_every==0 or i==len(tasks):
                emit('full_activation_capture_progress', completed=i, total=len(tasks), kind=kind)
    finally:
        del model
    emit('full_activation_capture_complete', root=str(root), tasks=len(tasks))


def parse_layers(text, layer_count):
    if text == 'all':
        return list(range(layer_count))
    try:
        layers = sorted(set(int(part) for part in text.split(',') if part.strip()))
    except ValueError as exc:
        raise ValueError('FULL_ACTIVATION_INVALID_LAYERS') from exc
    if not layers or min(layers)<0 or max(layers)>=layer_count:
        raise ValueError('FULL_ACTIVATION_INVALID_LAYERS')
    return layers


def design_matrix(root, kind, values, layers, identity):
    """Right-align full prompt positions. Never average or pool token states."""
    rows, num_tokens = [], []
    for n in values:
        key = multi.quantize(int(n)) if kind=='logbar' else int(n)
        prompt = multi.prompt(kind,key)
        meta, tensor = read_full(root,kind,key,prompt,identity)
        if max(layers)>=tensor.shape[0]:
            raise ValueError('FULL_ACTIVATION_LAYER_OUT_OF_RANGE')
        rows.append(tensor[layers].astype(np.float32,copy=False))
        num_tokens.append(tensor.shape[1])
    slots=max(num_tokens)
    width=rows[0].shape[-1]
    # Feature index (layer_index, negative token position from END, neuron).
    # Last token occupies slot -1 for all lengths. Unused leading slots are 0.
    p=len(layers)*slots*width
    # Dense matrix is intentional: all real token states remain accessible to L1.
    mat=np.zeros((len(values),p),dtype=np.float32)
    for i, tensor in enumerate(rows):
        length=tensor.shape[1]
        mat[i]=np.pad(tensor,((0,0),(slots-length,0),(0,0)),constant_values=0).reshape(-1)
    return mat, {'kind':kind,'layers':layers,'slots':slots,'width':width,
                 'feature_count':p, 'prompt_token_counts':num_tokens,
                 'slot_alignment':'right_aligned_full_prompt',
                 'token_pooling':'none'}


def standardize_design(x):
    """Affine feature scaling (not reduction); constant columns have no information."""
    x=np.asarray(x,dtype=np.float32)
    mu=x.mean(axis=0,dtype=np.float64).astype(np.float32)
    # var from centered copy is a large allocation but bounded by selected layers.
    scale=np.sqrt(np.mean((x-mu)**2,axis=0,dtype=np.float64)).astype(np.float32)
    valid=scale>1e-5
    z=np.ascontiguousarray((x[:,valid]-mu[valid])/scale[valid],dtype=np.float32)
    return z, np.flatnonzero(valid).astype(np.int32), mu[valid], scale[valid]


class SparseFitFailure(RuntimeError):
    """A numerically invalid head. Caller records it and moves to next penalty."""
    def __init__(self, code, **diagnostics):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics


def lasso_sparse(x, y, alpha_ratio, kkt_iters=12, tol=0.02,
                 solver_max_iter=10000, solver_retry_multiplier=3):
    """True LASSO with measured sparsity and checked solver + omitted-feature KKT.

    L1 penalty is an input; nonzero coefficient count is an observed OUTPUT.
    All nonconstant columns remain eligible. Locally capture sklearn warnings,
    retry the solver from its previous coefficients, and NEVER certify a head
    when either its active solve or its omitted-feature KKT test failed.
    """
    from sklearn.linear_model import Lasso
    from sklearn.exceptions import ConvergenceWarning
    x = np.asarray(x, dtype=np.float32)
    y = np.asarray(y, dtype=np.float64)
    n, p = x.shape
    if (not (0 < alpha_ratio <= 1) or not p or not n or len(y) != n or
            not np.isfinite(y).all() or solver_max_iter < 1 or
            solver_retry_multiplier < 1 or kkt_iters < 1):
        raise ValueError('SPARSE_HEAD_INVALID_MATRIX_OR_ALPHA')
    ym = float(y.mean())
    spread = float(np.std(y))
    if spread < 1e-12:
        return {'weights':np.empty(0), 'indices':np.empty(0,dtype=np.int32),
                'intercept':ym,'alpha':0.,'alpha_max':0.,
                'alpha_ratio':float(alpha_ratio),'nonzero':0,'kkt_passed':True,
                'kkt_violation':0.,'training_samples':n,'solver_converged':True,
                'solver_warning_count':0,'solver_warning_fits':0,
                'solver_fit_calls':0,'solver_last_iterations':0,
                'solver_last_dual_gap':0.}
    yn = (y-ym)/spread
    xmean = x.mean(axis=0,dtype=np.float64)
    cross = (x.T@yn)/n - xmean*float(yn.mean())
    order = np.argsort(-np.abs(cross),kind='stable')
    alpha_max = float(np.max(np.abs(cross)))
    if alpha_max <= 1e-12:
        raise SparseFitFailure('SPARSE_HEAD_NO_CORRELATIONS',samples=n,features=p)
    alpha = float(alpha_ratio)*alpha_max
    active = np.sort(order[:min(p,128)]).astype(np.int32)
    violation = math.inf
    warning_count = warning_fits = fit_calls = 0
    last_iterations = 0
    last_gap = math.inf
    for iteration in range(kkt_iters):
        reg = Lasso(alpha=alpha,fit_intercept=True,selection='cyclic',
                    tol=1e-5,max_iter=solver_max_iter,warm_start=True)
        solver_converged = False
        # A bounded continuation of the *same* optimization problem. Do not
        # alter alpha, discard dimensions, or silently accept nonconvergence.
        for fit_attempt, limit in enumerate((solver_max_iter,
                solver_max_iter*solver_retry_multiplier)):
            if fit_attempt and limit == solver_max_iter:
                break
            reg.set_params(max_iter=limit)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always',ConvergenceWarning)
                reg.fit(x[:,active],yn)
            wcount = sum(issubclass(w.category,ConvergenceWarning) for w in caught)
            fit_calls += 1
            warning_count += wcount
            warning_fits += int(wcount>0)
            last_iterations = int(reg.n_iter_)
            last_gap = float(reg.dual_gap_)
            solver_converged = wcount==0 and math.isfinite(last_gap)
            if solver_converged:
                break
        if not solver_converged:
            raise SparseFitFailure('SPARSE_HEAD_SOLVER_NOT_CONVERGED',
                samples=n,features=p,active_features=len(active),
                alpha_ratio=float(alpha_ratio),alpha=alpha,
                kkt_iteration=iteration+1,solver_fit_calls=fit_calls,
                solver_warning_count=warning_count,
                solver_last_iterations=last_iterations,
                solver_last_dual_gap=last_gap)
        residue = yn-reg.predict(x[:,active])
        gradient = (x.T@residue)/n
        outside = np.ones(p,dtype=bool)
        outside[active] = False
        violation = float(np.max(np.maximum(0.,np.abs(gradient[outside])-alpha))) if outside.any() else 0.
        if violation <= alpha*tol+1e-10:
            selected = np.flatnonzero(np.abs(reg.coef_)>1e-7)
            coefficients = reg.coef_[selected].astype(np.float64)*spread
            return {'indices':active[selected].astype(np.int32),
                    'weights':coefficients,
                    'intercept':float(reg.intercept_*spread+ym),
                    'alpha':alpha,'alpha_max':alpha_max,
                    'alpha_ratio':float(alpha_ratio),
                    'nonzero':len(selected),'kkt_passed':True,
                    'kkt_violation':violation,'training_samples':n,
                    'solver_converged':True,
                    'solver_warning_count':int(warning_count),
                    'solver_warning_fits':int(warning_fits),
                    'solver_fit_calls':fit_calls,
                    'solver_last_iterations':last_iterations,
                    'solver_last_dual_gap':last_gap}
        new = np.flatnonzero(outside & (np.abs(gradient)>alpha*(1+tol)))
        if not len(new):
            # Numerical differences can leave violations between the exact
            # and tolerance-adjusted threshold. The head is NOT certified.
            raise SparseFitFailure('SPARSE_HEAD_KKT_NOT_CONVERGED',
                samples=n,features=p,active_features=len(active),
                alpha_ratio=float(alpha_ratio),alpha=alpha,
                kkt_iteration=iteration+1,kkt_violation=violation,
                solver_warning_count=warning_count)
        new = new[np.argsort(-np.abs(gradient[new]),kind='stable')[:max(128,len(active))]]
        active = np.sort(np.unique(np.concatenate((active,new))))
    raise SparseFitFailure('SPARSE_HEAD_KKT_NOT_CONVERGED',
        samples=n,features=p,active_features=len(active),
        alpha_ratio=float(alpha_ratio),alpha=alpha,
        kkt_iterations=kkt_iters,kkt_violation=violation,
        solver_warning_count=warning_count,solver_fit_calls=fit_calls)


class SparseHeadFactory:
    """Adapter to the existing sign/regime/expert hierarchy; each head is true L1."""
    def __init__(self, features, alpha_ratio, solver_max_iter=10000,
                 solver_retry_multiplier=3, kkt_iters=12):
        self.solver_max_iter=solver_max_iter
        self.solver_retry_multiplier=solver_retry_multiplier
        self.kkt_iters=kkt_iters
        self.features=np.asarray(features,dtype=np.float32)
        self.df=float(alpha_ratio)  # compatibility with hierarchy interface
        self.heads={}

    def fit(self, name, subset, response):
        mask=np.asarray(subset,dtype=bool)
        if not mask.any():
            raise ValueError('SPARSE_HEAD_EMPTY_SUBSET')
        try:
            fit=lasso_sparse(self.features[mask],response,self.df,
                             kkt_iters=self.kkt_iters,solver_max_iter=self.solver_max_iter,
                             solver_retry_multiplier=self.solver_retry_multiplier)
        except SparseFitFailure as exc:
            exc.diagnostics['head']=name
            raise
        indices=fit['indices']; weights=fit['weights']
        estimates=self.features[:,indices]@weights+fit['intercept']
        self.heads[name]={**fit,'effective_df':float(fit['nonzero']),
                          'rank':min(int(mask.sum())-1,int(self.features.shape[1])),
                          'lasso_alpha':fit['alpha'], 'alpha_ratio':fit['alpha_ratio']}
        return estimates


def save_heads(directory, heads, columns, means, scales, layout):
    directory.mkdir(parents=True,exist_ok=True)
    records=[]
    for name,head in heads.items():
        selected=np.asarray(head['indices'],dtype=np.int32)
        path=directory/(name+'.npz')
        np.savez_compressed(path, feature_indices=columns[selected],
                            weights=np.asarray(head['weights'],dtype=np.float64),
                            intercept=np.float64(head['intercept']),
                            feature_means=means[selected], feature_scales=scales[selected])
        records.append({'head':name,'nonzero':head['nonzero'],
                        'training_samples':head['training_samples'],
                        'lasso_alpha':head['alpha'], 'alpha_max':head['alpha_max'],
                        'alpha_ratio':head['alpha_ratio'],
                        'kkt_passed':head['kkt_passed'],
                        'kkt_violation':head['kkt_violation'],
                        'solver_converged':head['solver_converged'],
                        'solver_warning_count':head['solver_warning_count'],
                        'solver_warning_fits':head['solver_warning_fits'],
                        'solver_fit_calls':head['solver_fit_calls'],
                        'solver_last_iterations':head['solver_last_iterations'],
                        'solver_last_dual_gap':head['solver_last_dual_gap'],
                        'coefficients_file':str(path.name)})
    dec.write_json(directory/'layout.json',layout)
    dec.write_json(directory/'heads.json',records)


def decode(args):
    root=args.output_root.expanduser().resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    identity=json.loads((root/'model_identity.json').read_text(encoding='utf-8'))
    values=np.asarray(manifest['values'],dtype=np.int64)
    kinds=tuple(item.strip() for item in args.representations.split(',') if item.strip())
    if not kinds or any(k not in KINDS for k in kinds):
        raise ValueError('SPARSE_BAD_REPRESENTATIONS')
    alpha_ratios=tuple(float(v) for v in args.alpha_ratios.split(',') if v.strip())
    if not alpha_ratios or any(not 0 < a <= 1 for a in alpha_ratios) or len(set(alpha_ratios))!=len(alpha_ratios):
        raise ValueError('SPARSE_INVALID_ALPHA_RATIO_LIST')
    first=next(iter(values))
    key=multi.quantize(int(first)) if kinds[0]=='logbar' else int(first)
    _,raw=read_full(root,kinds[0],key,multi.prompt(kinds[0],key),identity)
    layers=parse_layers(args.layers,raw.shape[0])
    summary=[]; failures=[]; prediction_rows=[]
    def checkpoint():
        # Persist after each penalty; earlier results survive a later failure.
        if summary:
            dec.write_csv(root/'lasso_penalty_curve.csv',summary,list(summary[0]))
        if prediction_rows:
            dec.write_csv(root/'lasso_penalty_predictions.csv',prediction_rows,list(prediction_rows[0]))
        dec.write_json(root/'lasso_penalty_failures.json',failures)
        report={'schema':SCHEMA,'decoder_version':'lasso_penalty_path_v3',
                'scope':'in_sample_only','success':'zero exact and each nonzero abs(predicted-x)/abs(x) <= 0.05',
                'no_token_averaging':True,
                'lasso_kkt_verified_on_valid_rows':all(r['kkt_verified'] for r in summary),
                'solver_warning_count_valid_rows':sum(r['solver_warning_count'] for r in summary),
                'failed_stages':len(failures),
                'solver_warning_policy':'captured locally; failures explicit; no uncertified row accepted',
                'feature_alignment':'right aligned full prompt, fixed slots per representation',
                'total_complexity':'sum of observed nonzero coefficients across all fitted heads',
                'penalty_rule':'each head alpha = alpha_ratio * head-specific alpha_max',
                'alpha_ratios':list(alpha_ratios),'layers':layers,'results':summary,'failures':failures,
                'completed_penalty_points':sum(1 for r in summary if r.get('final_for_penalty')),
                'status':'partial' if failures else 'valid_results_only'}
        dec.write_json(root/'lasso_penalty_summary.json',report)
        return report

    for kind in kinds:
        matrix,layout=design_matrix(root,kind,values,layers,identity)
        z,columns,means,scales=standardize_design(matrix)
        del matrix
        emit('sparse_design_ready',representation=kind,samples=len(values),
             full_features=layout['feature_count'],nonconstant_features=z.shape[1],
             layers=layers,token_slots=layout['slots'],averaging=False)
        for alpha_ratio in alpha_ratios:
            winner=None; stage_count=0; failed=None
            for stage,cuts in enumerate(hier.REGIMES[:args.max_regimes-1],1):
                if not any(np.abs(values)>cuts[-1]):
                    continue
                stage_count+=1
                fac=SparseHeadFactory(z,alpha_ratio,
                    solver_max_iter=args.solver_max_iter,
                    solver_retry_multiplier=args.solver_retry_multiplier,
                    kkt_iters=args.kkt_iters)
                try:
                    fitted=hier.fit_hierarchy(z,values.astype(np.float64),cuts,float(alpha_ratio),factory=fac)
                except SparseFitFailure as exc:
                    failed={'representation':kind,'alpha_ratio':float(alpha_ratio),
                            'stage':stage,'regimes':len(cuts)+1,'cuts':list(cuts),
                            'status':'invalid_fit','reason':exc.code,
                            'details':exc.diagnostics,
                            'heads_completed_before_failure':len(fac.heads)}
                    failures.append(failed)
                    break  # later stages would reuse the failing head
                metric,passing,rel=hier.metrics(values,fitted['predicted'],cuts)
                oracle,_,_=hier.metrics(values,fitted['oracle'],cuts)
                row={'representation':kind,'alpha_ratio':float(alpha_ratio),
                     'stage':stage,'regimes':len(cuts)+1,'cuts':list(cuts),
                     'layers':layers,'pass_rate':metric['within_5pct_rate'],
                     'all_pass':metric['all_probes_pass'],'zero_exact':metric['zero_exact'],
                     'sign_accuracy':metric['sign_accuracy'],
                     'mae':metric['mae'],'max_relative_error':metric['max_relative_error'],
                     'oracle_pass_rate':oracle['within_5pct_rate'],
                     'oracle_all_pass':oracle['all_probes_pass'],
                     'coefficient_count':int(sum(h['nonzero'] for h in fac.heads.values())),
                     'alpha_min':float(min(h['alpha'] for h in fac.heads.values())),
                     'alpha_max':float(max(h['alpha'] for h in fac.heads.values())),
                     'heads':len(fac.heads),
                     'kkt_verified':all(h['kkt_passed'] for h in fac.heads.values()),
                     'nonconverged_heads':sum(not h['solver_converged'] for h in fac.heads.values()),
                     'heads_with_solver_warnings':sum(h['solver_warning_count']>0 for h in fac.heads.values()),
                     'solver_warning_count':sum(h['solver_warning_count'] for h in fac.heads.values()),
                     'final_for_penalty':False}
                summary.append(row)
                winner=(row,fitted,fac.heads,passing,rel)
                if metric['all_probes_pass']:
                    break
            if winner is not None:
                row,fitted,heads,passing,rel=winner
                row['final_for_penalty']=(failed is None)
                alpha_key=f'{alpha_ratio:.6g}'.replace('.','p')
                target=root/'sparse_models'/kind/f'alpha_{alpha_key}_stage_{row["stage"]}'
                save_heads(target,heads,columns,means,scales,layout)
                for n,predicted,oracle,pas,relative in zip(values,fitted['predicted'],fitted['oracle'],passing,rel):
                    prediction_rows.append({'representation':kind,'alpha_ratio':alpha_ratio,'stage':row['stage'],
                                            'number':int(n),'prediction':float(predicted),
                                            'oracle_prediction':float(oracle),
                                            'within_5pct':bool(pas),
                                            'relative_error':float(relative) if n else '',
                                            'abs_error':float(abs(predicted-n))})
            checkpoint()
            emit('lasso_penalty_progress',representation=kind,alpha_ratio=float(alpha_ratio),
                 attempted_stages=stage_count,
                 valid_stages=stage_count-int(failed is not None),
                 best_pass_rate=winner[0]['pass_rate'] if winner else None,
                 actual_nonzero_coefficients=winner[0]['coefficient_count'] if winner else None,
                 all_pass=bool(winner and winner[0]['all_pass']),
                 fit_failure=failed['reason'] if failed else None,
                 fit_failure_head=failed['details'].get('head') if failed else None,
                 solver_warnings=winner[0]['solver_warning_count'] if winner else 0)
        del z
    report=checkpoint()
    emit('lasso_penalty_hierarchy_complete',root=str(root),valid_stages=len(summary),
         failed_stages=len(failures),valid_penalty_points=report['completed_penalty_points'],
         winning=[{'representation':r['representation'],'alpha_ratio':r['alpha_ratio'],
                   'stage':r['stage'],'coefficient_count':r['coefficient_count']}
                  for r in summary if r['all_pass']])
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--capture-only',action='store_true')
    p.add_argument('--analyze-only',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--model',choices=tuple(cassette.SOURCE),default='qwen')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--max-prompt-tokens',type=int,default=128)
    p.add_argument('--store-dtype',choices=('float16','float32'),default='float16')
    p.add_argument('--report-every',type=int,default=20)
    p.add_argument('--limit',type=int,default=0)
    p.add_argument('--layers',default='16,28',help='Layers jointly supplied to each decoder, or all')
    p.add_argument('--representations',default='decimal,logbar,paired')
    p.add_argument('--alpha-ratios',default=','.join(map(str,DEFAULT_ALPHA_RATIOS)),
                   help='LASSO penalty fractions of each head-specific alpha_max; count is measured, not targeted')
    p.add_argument('--solver-max-iter',type=int,default=10000,
                   help='Initial coordinate-descent iteration limit before warm-start retry')
    p.add_argument('--solver-retry-multiplier',type=int,default=3,
                   help='Retry iteration multiplier; 1 disables retry')
    p.add_argument('--kkt-iters',type=int,default=12,
                   help='Maximum active-set expansion passes before marking a head invalid')
    p.add_argument('--max-regimes',type=int,default=6)
    p.add_argument('--output-root',type=Path,default=ROOT)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        assert len(multi.grid())==507
        assert len(DEFAULT_ALPHA_RATIOS)==12 and DEFAULT_ALPHA_RATIOS[0]==1.0
        emit('full_activation_lasso_alpha_self_test_passed')
        return
    if args.solver_max_iter<1 or args.solver_retry_multiplier<1 or args.kkt_iters<1:
        raise ValueError('SPARSE_BAD_SOLVER_ARGUMENTS')
    if args.max_regimes<2 or args.max_regimes>len(hier.REGIMES)+1:
        raise ValueError('SPARSE_BAD_REGIME_LIMIT')
    if args.report_every<1 or args.max_prompt_tokens<8:
        raise ValueError('SPARSE_BAD_CAPTURE_ARGUMENTS')
    capture(args)
    if not (args.capture_only or args.prepare_only):
        decode(args)


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as exc:
        emit('full_activation_sparse_error',message=str(exc))
        raise SystemExit(1)
