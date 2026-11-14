#!/usr/bin/env python3
"""Two-number frozen-Qwen activation reconstruction (IN SAMPLE).

One prompt contains two ordered values separated by a literal newline. Three
independent input encodings: decimal, logbar, and paired decimal+logbar.
Capture ALL token-level states for ALL layers without pooling; decoder flattens
chosen layers with right-aligned zero padding and uses actual LASSO penalties.
Two independent sign/regime/magnitude hierarchies decode x1 and x2, scored
JOINTLY at <= 5% error relative to each original nonzero value; zero is exact.

Requires earlier scripts in the same tools directory:
 nanojev_full_activation_sparse_hierarchy.py (latest penalty/convergence fix)
 nanojev_multiscale_activation_decoder.py
 nanojev_hierarchical_activation_decoder.py
 and their standard dependencies. No training of the frozen Qwen model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import nanojev_full_activation_sparse_hierarchy as sparse

SCHEMA = 'nanojev.two-number-full-token-lasso.v1'
DEFAULT_ROOT = Path.home() / 'NanoJev' / 'runs' / 'two_number_sparse_v1'
KINDS = ('decimal', 'logbar', 'paired')
# Low-penalty region was informative in the one-number experiment. Fractions of
# each LASSO head's independently computed maximum penalty, not target counts.
DEFAULT_ALPHAS = (0.03, 0.015, 0.005, 0.002)


def emit(event, **values):
    sparse.emit(event, **values)


def prompt(kind, pair):
    """No position labels or target leakage: precisely one newline separator."""
    a, b = map(int, pair)
    if kind == 'decimal':
        return f'{a}\n{b}'
    if kind == 'logbar':
        return f'{sparse.multi.encode(a)}\n{sparse.multi.encode(b)}'
    if kind == 'paired':
        return f'{a}, {sparse.multi.encode(a)}\n{b}, {sparse.multi.encode(b)}'
    raise ValueError('TWO_NUMBER_INVALID_REPRESENTATION')


def generate_pairs(count=2000, seed=20261012):
    """Ordered, reproducible, distinct numeric pairs with swapped-order controls.

    507 original numerical values appear in both positions (at default size).
    Mix distinct magnitudes/signs, equal/nearby, zero and extreme contrasts.
    Every base pair contributes a reversed-order observation, so neither
    position receives a privileged distribution of values.
    """
    if count < 20 or count % 2:
        raise ValueError('TWO_NUMBER_PAIR_COUNT_MUST_BE_EVEN_AT_LEAST_20')
    grid = list(sparse.multi.grid())
    if count // 2 > (len(grid) * len(grid) + len(grid)) // 2:
        raise ValueError('TWO_NUMBER_TOO_MANY_PAIRS')
    rng = np.random.default_rng(seed)
    members = set(grid)
    selected, seen = [], set()
    def add(a, b):
        a, b = int(a), int(b)
        if a not in members or b not in members:
            raise ValueError('TWO_NUMBER_PAIR_NOT_ON_GRID')
        key = tuple(sorted((a, b)))
        if key in seen:
            return False
        seen.add(key)
        selected.append((a, b))
        return True
    # Curated adversarial patterns: sign conflicts, huge contrasts, zeros,
    # close neighbors, repetitions, and near-zero values.
    anchors = [(2_000_000,-3), (-2_000_000,1), (0,2_000_000),
               (0,-2_000_000), (17,19), (-17,-19), (1,-1),
               (1,1), (0,0), (2_000_000,2_000_000),
               (-2_000_000,-2_000_000), (1000,1000),
               (200,2000), (-200,2000), (3,-2_000_000)]
    for a,b in anchors[:count//2]:
        add(a,b)
    # Coverage pass: every grid value participates. For 2000 probes every
    # grid value will appear in both FIRST and SECOND positions after reversal.
    shuffled = rng.permutation(grid)
    for a,b in zip(grid,shuffled):
        if len(selected) >= count//2: break
        add(a,b)
    # Stratified new pairs. Unlike a Cartesian grid, draws prioritize pairs
    # that differ sharply in magnitude, share magnitude, or lie near zero.
    bands = [np.asarray([v for v in grid if abs(v) <= 20]),
             np.asarray([v for v in grid if 20 < abs(v) <= 200]),
             np.asarray([v for v in grid if 200 < abs(v) <= 2000]),
             np.asarray([v for v in grid if 2000 < abs(v) <= 20000]),
             np.asarray([v for v in grid if 20000 < abs(v) <= 200000]),
             np.asarray([v for v in grid if abs(v) > 200000])]
    trials = 0
    while len(selected) < count//2:
        trials += 1
        if trials > count * 200:
            raise RuntimeError('TWO_NUMBER_PAIR_GENERATION_EXHAUSTED')
        mode = trials % 6
        if mode == 0:  # extreme contrast
            u,v = 0,5
        elif mode == 1:  # same large-magnitude band
            u=v=int(rng.integers(2,6))
        elif mode == 2:  # one near zero
            u,v=0,int(rng.integers(1,6))
        elif mode == 3:  # mixed independent magnitude bands
            u,v=map(int,rng.integers(0,6,size=2))
        elif mode == 4:  # middle scales
            u,v=map(int,rng.integers(1,5,size=2))
        else:  # completely independent, magnitude-biased by grid density
            u=v=-1
        if u < 0:
            a,b=map(int,rng.choice(grid,size=2))
        else:
            a=int(rng.choice(bands[u])); b=int(rng.choice(bands[v]))
        add(a,b)
    # Exactly two ordered observations for each unordered non-identical pair.
    # Equal-value prompts are intrinsically invariant under reversal: emit one
    # repeat only, which is recorded as a duplicate probe (not a new prompt).
    ordered=[]
    for a,b in selected:
        ordered.extend([(a,b),(b,a)])
    rng.shuffle(ordered)
    assert len(ordered)==count
    return ordered


def plan(args):
    pairs=[list(pair) for pair in generate_pairs(args.pair_count,args.seed)]
    tasks={}
    indexes={}
    for kind in args.representations.split(','):
        kind=kind.strip()
        if kind not in KINDS:
            raise ValueError('TWO_NUMBER_BAD_REPRESENTATION')
        for pair in pairs:
            content=prompt(kind,pair)
            digest=hashlib.sha256(content.encode('utf-8')).hexdigest()
            tasks[(kind,digest)]=content
            indexes.setdefault(kind,[]).append(digest)
    settings={'schema':SCHEMA,'pair_count':len(pairs),'seed':args.seed,
              'pairs':pairs,'representations':list(indexes),
              'task_digests':indexes,
              'capture':{'model':args.model,
                         'model_source':sparse.cassette.SOURCE[args.model][0],
                         'model_revision':sparse.cassette.SOURCE[args.model][1],
                         'store_dtype':args.store_dtype,
                         'max_prompt_tokens':args.max_prompt_tokens},
              'prompt_format':'number1\\nnumber2 (each line encoded independently)'}
    return settings,tasks


def record_path(root,kind,digest):
    return root/'records'/f'{kind}_{digest}.npz'


def read_record(root,kind,digest,expected,identity):
    path=record_path(root,kind,digest)
    if not path.is_file():
        raise FileNotFoundError(f'TWO_NUMBER_MISSING_RECORD:{path}')
    with np.load(path,allow_pickle=False) as f:
        md=json.loads(str(f['metadata'].item()))
        tensor=np.asarray(f['hidden_states'])
    if (md.get('prompt')!=expected or
        md.get('sha256')!=sparse.dec.digest(expected) or
        md.get('identity')!=identity or
        md.get('shape')!=list(tensor.shape) or tensor.ndim!=3 or
        tensor.shape[1]!=len(md.get('input_ids',[])) or
        not np.isfinite(tensor).all()):
        raise RuntimeError(f'TWO_NUMBER_RECORD_DRIFT:{path}')
    return md,tensor


def capture(args):
    settings,tasks=plan(args)
    root=args.output_root.expanduser().resolve()
    if args.prepare_only:
        emit('two_number_plan',pairs=len(settings['pairs']),
             unique_prompts=len(tasks),unique_by_kind={k:len(set(settings['task_digests'][k])) for k in settings['representations']},
             examples=[{'representation':k,'prompt':prompt(k,(2_000_000,-3))} for k in settings['representations']])
        return settings
    manifest=root/'manifest.json'
    if manifest.is_file():
        existing=json.loads(manifest.read_text(encoding='utf-8'))
        if existing!=settings and not args.analyze_only:
            raise RuntimeError('TWO_NUMBER_MANIFEST_DRIFT')
        if args.analyze_only:
            settings=existing
        if not args.resume and not args.analyze_only:
            raise RuntimeError('TWO_NUMBER_EXISTING_RUN_USE_RESUME')
    elif args.analyze_only:
        raise FileNotFoundError(f'TWO_NUMBER_MANIFEST_MISSING:{manifest}')
    elif root.exists() and any(root.iterdir()):
        raise RuntimeError('TWO_NUMBER_OUTPUT_NOT_EMPTY')
    if args.analyze_only:
        return settings
    identity_path=root/'model_identity.json'
    existing_id=json.loads(identity_path.read_text(encoding='utf-8')) if identity_path.is_file() else None
    needed=[(kind,digest,content) for (kind,digest),content in tasks.items()
            if not record_path(root,kind,digest).exists()]
    if not needed:
        if existing_id is None:
            raise RuntimeError('TWO_NUMBER_MISSING_IDENTITY')
        for (kind,digest),content in tasks.items():
            read_record(root,kind,digest,content,existing_id)
        emit('two_number_capture_already_complete',prompts=len(tasks))
        return settings
    torch,model,tokenizer,device,info=sparse.cassette.load_source(args.model,args)
    identity={'model':args.model,'model_info':info}
    try:
        if existing_id is not None and existing_id!=identity:
            raise RuntimeError('TWO_NUMBER_MODEL_IDENTITY_DRIFT')
        (root/'records').mkdir(parents=True,exist_ok=True)
        if not manifest.is_file(): sparse.dec.write_json(manifest,settings)
        if not identity_path.is_file(): sparse.dec.write_json(identity_path,identity)
        for i,((kind,digest),content) in enumerate(tasks.items(),1):
            path=record_path(root,kind,digest)
            if path.is_file():
                if not args.resume:
                    raise RuntimeError(f'TWO_NUMBER_DUPLICATE:{path}')
                read_record(root,kind,digest,content,identity)
            else:
                meta,tensor=sparse.capture_full(torch,model,tokenizer,content,device,
                                                args.max_prompt_tokens,args.store_dtype)
                meta['identity']=identity
                tmp=path.with_name(path.name+'.partial')
                with tmp.open('wb') as f:
                    np.savez_compressed(f,metadata=json.dumps(meta,sort_keys=True),hidden_states=tensor)
                tmp.replace(path)
            if i%args.report_every==0 or i==len(tasks):
                emit('two_number_capture_progress',completed=i,total=len(tasks),representation=kind)
    finally:
        del model
    emit('two_number_capture_complete',prompts=len(tasks),root=str(root))
    return settings


def matrix(root,kind,settings,layers,identity):
    digests=settings['task_digests'][kind]
    distinct={}
    lengths=[]
    for digest in digests:
        if digest not in distinct:
            path=record_path(root,kind,digest)
            with np.load(path,allow_pickle=False) as f:
                meta=json.loads(str(f['metadata'].item()))
            # Compare content hashes against independently recomputed prompts
            if meta.get('sha256')!=digest:
                raise RuntimeError('TWO_NUMBER_MISMATCHED_PROMPT_DIGEST')
            distinct[digest]=int(meta['input_token_count'])
        lengths.append(distinct[digest])
    slots=max(lengths)
    # Avoid repeated compressed loads when Log-Bar collisions yield duplicates.
    cache={}
    width=None
    arr=None
    for i,(pair,digest) in enumerate(zip(settings['pairs'],digests)):
        if digest not in cache:
            _,tensor=read_record(root,kind,digest,prompt(kind,pair),identity)
            if max(layers)>=tensor.shape[0]:
                raise ValueError('TWO_NUMBER_LAYER_OUT_OF_RANGE')
            chosen=np.asarray(tensor[layers],dtype=np.float32)
            cache[digest]=chosen
        chosen=cache[digest]
        if arr is None:
            width=chosen.shape[-1]
            arr=np.zeros((len(digests),len(layers)*slots*width),dtype=np.float32)
        if chosen.shape[-1]!=width:
            raise RuntimeError('TWO_NUMBER_HIDDEN_WIDTH_DRIFT')
        arr[i]=np.pad(chosen,((0,0),(slots-chosen.shape[1],0),(0,0))).reshape(-1)
    return arr, {'layers':layers,'slots':slots,'neuron_width':width,
                 'full_features':arr.shape[1],'samples':len(digests),
                 'alignment':'right aligned full prompt','pooling':'none',
                 'independent_representation':kind,'duplicated_prompts':len(digests)-len(cache)}


def joint_metrics(pairs,predictions):
    truth=np.asarray(pairs,dtype=np.float64)
    pred=np.asarray(predictions,dtype=np.float64)
    if truth.ndim!=2 or truth.shape[1]!=2 or pred.shape!=truth.shape:
        raise ValueError('TWO_NUMBER_INVALID_PREDICTIONS')
    rel=np.full_like(truth,np.nan)
    nonzero=truth!=0
    rel[nonzero]=np.abs(pred[nonzero]-truth[nonzero])/np.abs(truth[nonzero])
    passing=(nonzero & (np.nan_to_num(rel,nan=np.inf)<=.05+1e-12)) | (~nonzero & (pred==0))
    per_slot=passing.mean(axis=0)
    joint=np.all(passing,axis=1)
    sign_ok=np.where(truth==0,pred==0,np.sign(truth)==np.sign(pred))
    contrast=np.maximum(np.abs(truth[:,0]),np.abs(truth[:,1]))/np.maximum(1,np.minimum(np.abs(truth[:,0]),np.abs(truth[:,1])))
    high=contrast>=1000
    # A reversed prompt is a different forward pass. Test whether the same
    # underlying values are reconstructed consistently across token positions.
    positions={tuple(map(int,p)):i for i,p in enumerate(truth) if p[0]!=p[1]}
    swap_checks=[]
    for pair,i in positions.items():
        j=positions.get((pair[1],pair[0]))
        if j is None or i>=j:
            continue
        a,b=pair
        error_a=abs(pred[i,0]-pred[j,1])/abs(a) if a else float(pred[i,0]!=pred[j,1])
        error_b=abs(pred[i,1]-pred[j,0])/abs(b) if b else float(pred[i,1]!=pred[j,0])
        swap_checks.append((error_a,error_b))
    swaps=np.asarray(swap_checks,dtype=float).reshape((-1,2))
    swap_consistency=float(np.mean(np.all(swaps<=0.05,axis=1))) if len(swaps) else None
    return {'pair_count':len(pairs),'both_within_5pct_rate':float(joint.mean()),
            'both_within_5pct_count':int(joint.sum()),'all_pairs_pass':bool(joint.all()),
            'slot_1_pass_rate':float(per_slot[0]),'slot_2_pass_rate':float(per_slot[1]),
            'slot_1_sign_accuracy':float(sign_ok[:,0].mean()),
            'slot_2_sign_accuracy':float(sign_ok[:,1].mean()),
            'both_sign_accuracy':float(np.all(sign_ok,axis=1).mean()),
            'extreme_ratio_1000x_count':int(high.sum()),
            'extreme_ratio_1000x_joint_pass_rate':float(joint[high].mean()) if high.any() else None,
            'reversed_pair_comparisons':int(len(swaps)),
            'reversed_pair_within_5pct_consistency':swap_consistency,
            'mean_absolute_errors':[float(z) for z in np.abs(pred-truth).mean(axis=0)],
            'zero_exact':bool(np.all(pred[truth==0]==0)),
            'max_relative_error':float(np.nanmax(rel)) if nonzero.any() else 0.},passing,rel


def analyze(args):
    root=args.output_root.expanduser().resolve()
    settings=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    identity=json.loads((root/'model_identity.json').read_text(encoding='utf-8'))
    all_pairs=np.asarray(settings['pairs'],dtype=np.int64)
    kinds=tuple(k.strip() for k in args.representations.split(',') if k.strip())
    if not kinds or any(k not in settings['representations'] for k in kinds):
        raise ValueError('TWO_NUMBER_BAD_REPRESENTATION')
    ratios=tuple(float(s) for s in args.alpha_ratios.split(',') if s.strip())
    if not ratios or any(not 0<r<=1 for r in ratios):
        raise ValueError('TWO_NUMBER_BAD_ALPHA_RATIOS')
    # Load one sample only to identify max available layers.
    sample_kind=kinds[0]; sample_digest=settings['task_digests'][sample_kind][0]
    _,raw=read_record(root,sample_kind,sample_digest,prompt(sample_kind,all_pairs[0]),identity)
    layers=sparse.parse_layers(args.layers,raw.shape[0])
    rows=[]; failures=[]; prediction_rows=[]
    def checkpoint():
        if rows: sparse.dec.write_csv(root/'two_number_fit_curve.csv',rows,list(rows[0]))
        if prediction_rows: sparse.dec.write_csv(root/'two_number_predictions.csv',prediction_rows,list(prediction_rows[0]))
        sparse.dec.write_json(root/'two_number_fit_failures.json',failures)
        sparse.dec.write_json(root/'two_number_summary.json',{
            'schema':SCHEMA,'scope':'in_sample_only','no_token_averaging':True,
            'per_value_tolerance':.05,'zero_exact_required':True,
            'pair_count':len(all_pairs),'layers':layers,'alpha_ratios':list(ratios),
            'status':'partial' if failures else 'completed_points_only',
            'results':rows,'failures':failures})
    for kind in kinds:
        x,layout=matrix(root,kind,settings,layers,identity)
        z,cols,means,scales=sparse.standardize_design(x)
        del x
        emit('two_number_design_ready',representation=kind,samples=len(all_pairs),
             full_features=layout['full_features'],nonconstant_features=z.shape[1],
             token_slots=layout['slots'],pooling=False)
        for ratio in ratios:
            winner=None; problem=None
            for stage,cuts in enumerate(sparse.hier.REGIMES[:args.max_regimes-1],1):
                if not np.any(np.abs(all_pairs)>cuts[-1]): continue
                predictions=np.zeros((len(all_pairs),2),dtype=np.float64)
                oracle=np.zeros_like(predictions)
                saved_heads={}
                try:
                    for slot in (0,1):
                        factory=sparse.SparseHeadFactory(z,ratio,
                            solver_max_iter=args.solver_max_iter,
                            solver_retry_multiplier=args.solver_retry_multiplier,
                            kkt_iters=args.kkt_iters)
                        fitted=sparse.hier.fit_hierarchy(z,all_pairs[:,slot].astype(np.float64),
                                                          cuts,float(ratio),factory=factory)
                        predictions[:,slot]=fitted['predicted']
                        oracle[:,slot]=fitted['oracle']
                        for key,head in factory.heads.items():
                            saved_heads[f'position_{slot+1}_{key}']=head
                except sparse.SparseFitFailure as exc:
                    problem={'representation':kind,'alpha_ratio':ratio,'stage':stage,
                             'code':exc.code,'details':exc.diagnostics}
                    failures.append(problem)
                    break
                score,passing,rel=joint_metrics(all_pairs,predictions)
                oracle_score,_,_=joint_metrics(all_pairs,oracle)
                coeffs=int(sum(h['nonzero'] for h in saved_heads.values()))
                result={'representation':kind,'alpha_ratio':float(ratio),'stage':stage,
                        'regimes':len(cuts)+1,'boundaries':list(cuts),
                        'pair_count':len(all_pairs),'joint_pass_rate':score['both_within_5pct_rate'],
                        'joint_pass_count':score['both_within_5pct_count'],
                        'all_pairs_pass':score['all_pairs_pass'],
                        'slot_1_pass_rate':score['slot_1_pass_rate'],
                        'slot_2_pass_rate':score['slot_2_pass_rate'],
                        'oracle_joint_pass_rate':oracle_score['both_within_5pct_rate'],
                        'both_sign_accuracy':score['both_sign_accuracy'],
                        'zero_exact':score['zero_exact'],
                        'extreme_ratio_1000x_count':score['extreme_ratio_1000x_count'],
                        'extreme_ratio_1000x_joint_pass_rate':score['extreme_ratio_1000x_joint_pass_rate'],
                        'max_relative_error':score['max_relative_error'],
                        'reversed_pair_within_5pct_consistency':score['reversed_pair_within_5pct_consistency'],
                        'coefficient_count':coeffs,'fitted_heads':len(saved_heads),
                        'solver_warning_count':sum(h['solver_warning_count'] for h in saved_heads.values()),
                        'all_heads_converged':all(h['solver_converged'] and h['kkt_passed'] for h in saved_heads.values())}
                rows.append(result)
                winner=(result,predictions,oracle,passing,rel,saved_heads)
                if score['all_pairs_pass']: break
            if winner is not None:
                result,predictions,oracle,passing,rel,heads=winner
                # Keep only winning stage models; each representation independent.
                token=f'{ratio:.8g}'.replace('.','p')
                target=root/'two_number_models'/kind/f'alpha_{token}_stage_{result["stage"]}'
                sparse.save_heads(target,heads,cols,means,scales,layout)
                for i,(pair,pred,oracle_pred) in enumerate(zip(all_pairs,predictions,oracle)):
                    prediction_rows.append({'representation':kind,'alpha_ratio':float(ratio),
                        'stage':result['stage'],'pair_id':i,'x1':int(pair[0]),'x2':int(pair[1]),
                        'x1_hat':float(pred[0]),'x2_hat':float(pred[1]),
                        'x1_oracle':float(oracle_pred[0]),'x2_oracle':float(oracle_pred[1]),
                        'x1_relative_error':float(rel[i,0]) if pair[0]!=0 else '',
                        'x2_relative_error':float(rel[i,1]) if pair[1]!=0 else '',
                        'x1_pass':bool(passing[i,0]),'x2_pass':bool(passing[i,1]),
                        'both_pass':bool(passing[i].all())})
            checkpoint()
            emit('two_number_penalty_progress',representation=kind,alpha_ratio=float(ratio),
                 best_joint_pass_rate=winner[0]['joint_pass_rate'] if winner else None,
                 actual_nonzero_coefficients=winner[0]['coefficient_count'] if winner else None,
                 stage=winner[0]['stage'] if winner else None,
                 both_pass=winner[0]['all_pairs_pass'] if winner else False,
                 fit_failure=problem['code'] if problem else None,
                 solver_warnings=winner[0]['solver_warning_count'] if winner else 0)
        del z
    checkpoint()
    emit('two_number_complete',pairs=len(all_pairs),valid_stages=len(rows),failed_stages=len(failures),
         winning=[{'representation':r['representation'],'alpha_ratio':r['alpha_ratio'],
                   'stage':r['stage'],'coefficients':r['coefficient_count']}
                  for r in rows if r['all_pairs_pass']])
    return rows


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--prepare-only',action='store_true')
    p.add_argument('--capture-only',action='store_true')
    p.add_argument('--analyze-only',action='store_true')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    p.add_argument('--dtype',choices=('auto','float32','float16','bfloat16'),default='auto')
    p.add_argument('--model',choices=tuple(sparse.cassette.SOURCE),default='qwen')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--max-prompt-tokens',type=int,default=160)
    p.add_argument('--store-dtype',choices=('float16','float32'),default='float16')
    p.add_argument('--pair-count',type=int,default=2000)
    p.add_argument('--seed',type=int,default=20261012)
    p.add_argument('--report-every',type=int,default=50)
    p.add_argument('--layers',default='16,28')
    p.add_argument('--representations',default='decimal,logbar,paired')
    p.add_argument('--alpha-ratios',default=','.join(map(str,DEFAULT_ALPHAS)))
    p.add_argument('--max-regimes',type=int,default=6)
    p.add_argument('--solver-max-iter',type=int,default=10000)
    p.add_argument('--solver-retry-multiplier',type=int,default=3)
    p.add_argument('--kkt-iters',type=int,default=12)
    p.add_argument('--output-root',type=Path,default=DEFAULT_ROOT)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.self_test:
        assert len(generate_pairs(2000))==2000
        assert '\n' in prompt('paired',(2_000_000,-3))
        emit('two_number_self_test_passed')
        return
    if args.pair_count<20 or args.pair_count%2:
        raise ValueError('TWO_NUMBER_BAD_PAIR_COUNT')
    if args.max_regimes<2 or args.max_regimes>len(sparse.hier.REGIMES)+1:
        raise ValueError('TWO_NUMBER_BAD_REGIMES')
    if args.report_every<1 or args.solver_max_iter<1 or args.kkt_iters<1 or args.solver_retry_multiplier<1:
        raise ValueError('TWO_NUMBER_BAD_ARGUMENTS')
    capture(args)
    if not (args.capture_only or args.prepare_only):
        analyze(args)


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,FileNotFoundError,KeyError) as exc:
        emit('two_number_error',message=str(exc))
        raise SystemExit(1)
