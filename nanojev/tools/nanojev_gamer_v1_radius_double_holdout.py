#!/usr/bin/env python3
"""Read-only GAMER radius probe holding out BOTH game IDs and numeral values.

Two complementary value splits are evaluated: train {-4,-2,0,2,4} and
predict {-3,-1,1,3}; reverse the split to test unseen ZERO as well.  Split
values are computed from any symmetric integer radius capture with >= 2
nonzero even and odd values.  Every projection, scale, RBF bandwidth and
kernel coefficient is fit on training GAMES and training NUMERALS only.

Strict (inductive) results have no information whatsoever from the unseen
contexts in the fitted normalization. A separate transductive result subtracts
the mean of the UNLABELED held-out set from each held-out game; do not confuse
this with an individually deployable decoder or the strict result.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from tools.nanojev_gamer_v1_radius_recovery import _distance_squared, _bandwidth, _score, _sha256

SCHEMA = 'nanojev.gamer.v1.radius-double-holdout.v1'
DEFAULT_STAGES = (
    'embedding_last', 'embedding_mean100',
    'qwen_layer_07_mean100', 'qwen_final_last', 'qwen_final_mean100',
    'qwen_final_100', 'c0_input_norm', 'c0_hidden_1',
    'c0_hidden_2', 'c0_hidden_3', 'c0_bottleneck', 'c0_answer',
)


def _value_splits(values):
    v=np.asarray(values,dtype=np.int32)
    if v.ndim!=1 or len(v)<9 or len(set(map(int,v)))!=len(v) or 0 not in v or not np.array_equal(v,-v[::-1]):
        raise ValueError('GAMER_DOUBLE_HOLDOUT_REQUIRES_SYMMETRIC_RADIUS_AT_LEAST_FOUR')
    even=np.flatnonzero(v%2==0)
    odd=np.flatnonzero(v%2!=0)
    if len(even)<3 or len(odd)<4:
        raise ValueError('GAMER_DOUBLE_HOLDOUT_TOO_FEW_DISTINCT_TRAIN_VALUES')
    return [('even_to_odd',even,odd),('odd_to_even',odd,even)]


def _game_folds(games,folds,seed):
    # Group ALL contexts of the same game in the same fold.
    unique=np.array(sorted(set(games)),dtype=object)
    if len(unique)<max(8,folds*2):
        raise ValueError('GAMER_DOUBLE_HOLDOUT_TOO_FEW_DISTINCT_GAMES')
    perm=np.random.default_rng(seed).permutation(unique)
    heldout_groups=np.array_split(perm,folds)
    result=[]
    for k,group in enumerate(heldout_groups):
        held=np.flatnonzero(np.isin(games,group))
        train=np.flatnonzero(~np.isin(games,group))
        if len(held)==0 or len(train)<4:
            raise ValueError('GAMER_DOUBLE_HOLDOUT_EMPTY_FOLD')
        assert not set(games[i] for i in train).intersection(games[j] for j in held)
        result.append((k,train,held))
    return result


def _basis(raw,labels,rank):
    """Build a supervised slope/curvature subspace *only* from train examples.

    `labels` has shape [train_games, train_values]. A label shuffle re-fits
    this entire basis, avoiding the fixed-basis null leakage of older probes.
    """
    x=np.asarray(raw,dtype=np.float32)
    y=np.asarray(labels,dtype=np.float64)
    c,n,w=x.shape
    if y.shape!=(c,n):raise ValueError('GAMER_DOUBLE_HOLDOUT_BAD_TRAIN_LABELS')
    centered=x-x.mean(axis=1,keepdims=True)
    anchors=[]
    for degree in (1,2):
        feature=y**degree
        feature=feature-feature.mean(axis=1,keepdims=True)
        denom=np.sum(feature*feature,axis=1)
        slope=np.einsum('cn,cnd->cd',feature,centered,optimize=True)/np.maximum(denom[:,None],1e-20)
        norms=np.linalg.norm(slope.astype(np.float64),axis=1)
        good=norms>1e-9
        if np.any(good): anchors.append((slope[good]/norms[good,None]).astype(np.float32))
    if not anchors:return np.zeros((w,0),dtype=np.float32)
    a=np.concatenate(anchors,axis=0)
    gram=np.asarray(a@a.T,dtype=np.float64)
    eig,vec=np.linalg.eigh(gram)
    ids=np.argsort(eig)[::-1]
    largest=max(float(eig[ids[0]]),1e-20)
    keep=[int(i) for i in ids if eig[i]>max(1e-9,largest*1e-7)][:rank]
    if not keep:return np.zeros((w,0),dtype=np.float32)
    result=(vec[:,keep].T@a)/np.sqrt(eig[keep])[:,None]
    return np.asarray(result.T,dtype=np.float32)


def _features(raw,basis,center):
    # Caller explicitly chooses either training-only global center or
    # unlabeled per-context center; no fitting occurs here.
    d=np.asarray(raw,dtype=np.float32)-np.asarray(center,dtype=np.float32)
    return (d.reshape(-1,d.shape[-1])@basis).astype(np.float64)


def _decode(train_x,train_y,targets,*,ridge=1.0,kernel_ridge=0.20):
    """Fit identical linear/RBF decoders on train only and evaluate test sets."""
    if train_x.ndim!=2 or train_x.shape[0]!=len(train_y):raise ValueError('GAMER_DOUBLE_HOLDOUT_DECODER_SHAPE')
    lengths=[len(t) for t in targets]
    if train_x.shape[1]==0:
        return [(np.zeros(n),np.zeros(n)) for n in lengths]
    # Train-only scalar normalization to retain comparable model capacity.
    scale=np.maximum(np.std(train_x,axis=0),1e-7)
    x=train_x/scale
    t=np.concatenate(targets,axis=0)/scale
    coef=np.linalg.solve(x.T@x+ridge*np.eye(x.shape[1]),x.T@train_y)
    linear=t@coef
    gamma=_bandwidth(x)
    if gamma is None:
        nonlinear=np.zeros(len(t),dtype=np.float64)
    else:
        # Standard (non-anchored) RBF with an intercept; zero need NOT have
        # been seen during training, so f(0)=0 must not be hardwired.
        k=np.exp(-gamma*_distance_squared(x))
        k.flat[::len(x)+1]+=kernel_ridge
        weights=np.linalg.solve(k,train_y)
        nonlinear=np.exp(-gamma*_distance_squared(t,x))@weights
    out=[];offset=0
    for n in lengths:
        out.append((linear[offset:offset+n],nonlinear[offset:offset+n]))
        offset+=n
    return out


def _score_per_value(truth,pred,values):
    result={}
    for v in values:
        selected=truth==v
        p=np.asarray(pred[selected],dtype=np.float64)
        residual=p-float(v)
        result[str(int(v))]={'cases':int(np.sum(selected)),
            'mae':float(np.mean(np.abs(residual))) if len(p) else None,
            'mean_prediction':float(np.mean(p)) if len(p) else None,
            'bias':float(np.mean(residual)) if len(p) else None,
            'within_0_1':float(np.mean(np.abs(residual)<=0.1)) if len(p) else None,
            'within_1e_6':float(np.mean(np.abs(residual)<=1e-6)) if len(p) else None}
    return result


def _summarize(actual,pred,values):
    return dict(_score(actual,pred),per_value=_score_per_value(actual,pred,values))


def analyze_stage_double_holdout(arr,values,token_counts,games,*,folds=4,rank=12,controls=1,seed=47):
    a=np.asarray(arr)
    vals=np.asarray(values,dtype=np.int32)
    counts=np.asarray(token_counts)
    if a.ndim!=3 or counts.shape!=a.shape[:2] or a.shape[1]!=len(vals) or len(games)!=len(a):
        raise ValueError('GAMER_DOUBLE_HOLDOUT_SHAPE_MISMATCH')
    if not np.isfinite(a).all():raise ValueError('GAMER_DOUBLE_HOLDOUT_NONFINITE_ACTIVATIONS')
    # Every value must preserve token count; never silently compare shifted positions.
    valid=np.flatnonzero(np.all(counts==counts[:,[0]],axis=1))
    if len(set(games[i] for i in valid))<max(8,folds*2):
        return {'status':'INSUFFICIENT_MATCHED_GAME_CONTEXTS',
                'total_contexts':len(a),'eligible_contexts':len(valid)},None
    data=a[valid];game_names=np.asarray(games,dtype=object)[valid]
    group_folds=_game_folds(game_names,folds,seed)
    configurations=_value_splits(vals)
    output={};predictions={}
    for split,train_ix,test_ix in configurations:
        prediction={mode:{decoder:np.full((len(data),len(vals)),np.nan,dtype=np.float32)
                          for decoder in ('linear','nonlinear')}
                    for mode in ('inductive','unlabeled_context_centered')}
        null_predictions=[{mode:np.full((len(data),len(vals)),np.nan,dtype=np.float32)
                           for mode in ('inductive','unlabeled_context_centered')}
                          for _ in range(controls)]
        fold_records=[]
        for fold_id,train_games,test_games in group_folds:
            # No test GAME and no test VALUE appears in the fit.
            train_raw=np.asarray(data[np.ix_(train_games,train_ix)],dtype=np.float32)
            test_raw=np.asarray(data[np.ix_(test_games,test_ix)],dtype=np.float32)
            train_y=np.broadcast_to(vals[train_ix],train_raw.shape[:2]).astype(np.float64).copy()
            basis=_basis(train_raw,train_y,rank)
            global_train_mean=train_raw.mean(axis=(0,1),keepdims=True)
            train_game_mean=train_raw.mean(axis=1,keepdims=True)
            test_game_mean=test_raw.mean(axis=1,keepdims=True)
            train_strict=_features(train_raw,basis,global_train_mean)
            test_strict=_features(test_raw,basis,global_train_mean)
            train_centered=_features(train_raw,basis,train_game_mean)
            test_centered=_features(test_raw,basis,test_game_mean)
            strict=_decode(train_strict,train_y.ravel(),[test_strict])[0]
            contextual=_decode(train_centered,train_y.ravel(),[test_centered])[0]
            for mode,(lin,nonlin) in [('inductive',strict),('unlabeled_context_centered',contextual)]:
                prediction[mode]['linear'][np.ix_(test_games,test_ix)]=lin.reshape(len(test_games),len(test_ix))
                prediction[mode]['nonlinear'][np.ix_(test_games,test_ix)]=nonlin.reshape(len(test_games),len(test_ix))
            for ctrl in range(controls):
                random=np.random.default_rng(seed+100003*ctrl+1009*fold_id+(0 if split=='even_to_odd' else 9001))
                scrambled=np.stack([random.permutation(row) for row in train_y])
                # Refit the ENTIRE learned basis using shuffled training labels,
                # not just the final readout weights.
                null_basis=_basis(train_raw,scrambled,rank)
                null_strict_train=_features(train_raw,null_basis,global_train_mean)
                null_strict_test=_features(test_raw,null_basis,global_train_mean)
                null_context_train=_features(train_raw,null_basis,train_game_mean)
                null_context_test=_features(test_raw,null_basis,test_game_mean)
                ns=_decode(null_strict_train,scrambled.ravel(),[null_strict_test])[0][1]
                nc=_decode(null_context_train,scrambled.ravel(),[null_context_test])[0][1]
                null_predictions[ctrl]['inductive'][np.ix_(test_games,test_ix)]=ns.reshape(len(test_games),len(test_ix))
                null_predictions[ctrl]['unlabeled_context_centered'][np.ix_(test_games,test_ix)]=nc.reshape(len(test_games),len(test_ix))
            fold_records.append({'fold':fold_id,'training_game_indices':train_games.tolist(),
                'heldout_game_indices':test_games.tolist(),
                'training_game_names':sorted(set(game_names[train_games].tolist())),
                'heldout_game_names':sorted(set(game_names[test_games].tolist())),
                'train_values':vals[train_ix].tolist(),'heldout_values':vals[test_ix].tolist(),
                'basis_effective_rank':int(basis.shape[1])})
        y=np.broadcast_to(vals[test_ix],(len(data),len(test_ix))).ravel().astype(np.float64)
        split_result={'train_values':vals[train_ix].tolist(),'heldout_values':vals[test_ix].tolist(),
                      'zero_is_unseen':bool(0 in vals[test_ix]),'folds':fold_records,
                      'probe_basis_rank_requested':rank,'modes':{}}
        for mode in prediction:
            lin=prediction[mode]['linear'][:,test_ix].ravel().astype(np.float64)
            nonlin=prediction[mode]['nonlinear'][:,test_ix].ravel().astype(np.float64)
            if not np.isfinite(lin).all() or not np.isfinite(nonlin).all():
                raise ValueError('GAMER_DOUBLE_HOLDOUT_INCOMPLETE_FOLD_PREDICTIONS')
            nulls=[]
            for control in null_predictions:
                nul=control[mode][:,test_ix].ravel().astype(np.float64)
                if not np.isfinite(nul).all():raise ValueError('GAMER_DOUBLE_HOLDOUT_INCOMPLETE_NULL')
                nulls.append(_score(y,nul))
            split_result['modes'][mode]={'linear':_summarize(y,lin,vals[test_ix]),
               'nonlinear':_summarize(y,nonlin,vals[test_ix]),
               'retrained_shuffled_label_controls_nonlinear':nulls}
            for decoder in ('linear','nonlinear'):
                predictions[split+'__'+mode+'__'+decoder]=prediction[mode][decoder]
            for j,control in enumerate(null_predictions):
                predictions[split+'__'+mode+f'__null_{j}']=control[mode]
        output[split]=split_result
    return {'status':'COMPLETE','total_contexts':len(a),'eligible_contexts':len(valid),
            'excluded_contexts':len(a)-len(valid),'eligible_indices':valid.tolist(),
            'width':int(a.shape[-1]),'games':sorted(set(game_names.tolist())),
            'experiments':output},predictions


def _output_destination(source, explicit):
    if explicit is not None:
        dest=Path(explicit).resolve()
        if dest==source or source in dest.parents or dest in source.parents:
            raise ValueError('GAMER_DOUBLE_HOLDOUT_OUTPUT_OVERLAPS_SOURCE')
        if dest.exists():raise FileExistsError(dest)
        return dest
    root=source.with_name(source.name+'-double-holdout')
    for i in range(1,10000):
        dest=root.with_name(root.name+f'-{i:04d}')
        if not dest.exists():return dest
    raise RuntimeError('GAMER_DOUBLE_HOLDOUT_OUTPUT_SLOTS_EXHAUSTED')

def run_double_holdout(radius_dir,*,output=None,folds=4,rank=12,controls=1,seed=47,stages=None):
    source=Path(radius_dir).resolve()
    if folds<2 or folds>12 or rank<1 or rank>64 or controls<0 or controls>8:
        raise ValueError('GAMER_DOUBLE_HOLDOUT_BAD_BUDGET')
    required=[source/name for name in ('final_report.json','activations.npz','interventions.jsonl')]
    for file in required:
        if not file.is_file():raise FileNotFoundError(file)
    hashes={file.name:_sha256(file) for file in required}
    original=json.loads(required[0].read_text(encoding='utf-8'))
    if original.get('schema')!='nanojev.gamer.v1.radius-activations.v1' or original.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_DOUBLE_HOLDOUT_BAD_SOURCE_REPORT')
    if len(original.get('contexts',[]))<8:
        raise ValueError('GAMER_DOUBLE_HOLDOUT_NOT_ENOUGH_CONTEXTS')
    games=[c['game_id'] for c in original['contexts']]
    selected=tuple(stages) if stages is not None else DEFAULT_STAGES
    if not selected or len(set(selected))!=len(selected):raise ValueError('GAMER_DOUBLE_HOLDOUT_BAD_STAGES')
    with np.load(required[1],allow_pickle=False) as npz:
        vals=npz['values'].copy(); counts=npz['token_counts'].copy()
        _value_splits(vals)
        if len(counts)!=len(games) or counts.shape!=(len(games),len(vals)):
            raise ValueError('GAMER_DOUBLE_HOLDOUT_SOURCE_DIMENSIONS')
        for stage in selected:
            if stage not in npz.files:raise ValueError('GAMER_DOUBLE_HOLDOUT_STAGE_MISSING: '+stage)
        dest=_output_destination(source,output)
        # protect run and raw capture against writes, including explicit output.
        for protected in (source,original.get('expanded_run'),original.get('source_run')):
            if not protected:continue
            root=Path(protected).resolve()
            if dest==root or root in dest.parents or dest in root.parents:
                raise ValueError('GAMER_DOUBLE_HOLDOUT_OUTPUT_OVERLAPS_SOURCE')
        results={};pred={}
        for stage in selected:
            analysis,stage_predictions=analyze_stage_double_holdout(
                npz[stage],vals,counts,games,folds=folds,rank=rank,controls=controls,seed=seed)
            results[stage]=analysis
            if stage_predictions:
                for key,value in stage_predictions.items():pred[f'{stage}__{key}']=value
            if analysis['status']=='COMPLETE':
                eo=analysis['experiments']['even_to_odd']['modes']['inductive']['nonlinear']['r2_vs_zero']
                oe=analysis['experiments']['odd_to_even']['modes']['inductive']['nonlinear']['r2_vs_zero']
                ctx=analysis['experiments']['even_to_odd']['modes']['unlabeled_context_centered']['nonlinear']['r2_vs_zero']
                zero=analysis['experiments']['odd_to_even']['modes']['inductive']['nonlinear']['per_value']['0']['mae']
                print(f'  {stage}: strict_even_to_odd={eo:.4g} strict_odd_to_even={oe:.4g} '
                      f'unlabeled_center_even_to_odd={ctx:.4g} unseen_zero_MAE={zero:.4g}',flush=True)
            else: print(f'  {stage}: {analysis["status"]}',flush=True)
    for p in required:
        if hashes[p.name]!=_sha256(p):raise ValueError('GAMER_DOUBLE_HOLDOUT_SOURCE_MUTATED')
    dest.mkdir(parents=True,exist_ok=False)
    np.savez_compressed(dest/'double_holdout_predictions.npz',values=vals,**pred)
    summary={stage:{split:{mode:{decoder:metrics['modes'][mode][decoder]['r2_vs_zero']
                                      for decoder in ('linear','nonlinear')}
                                for mode in ('inductive','unlabeled_context_centered')}
                           for split,metrics in item['experiments'].items()}
                    for stage,item in results.items() if item['status']=='COMPLETE'}
    report={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY',
            'source_radius_run':str(source),'source_fingerprints_sha256':hashes,
            'expanded_run':original.get('expanded_run'),'checkpoint_step':original.get('checkpoint_step'),
            'radius_values':vals.tolist(),'folds':folds,'basis_rank':rank,
            'retrained_null_controls':controls,'seed':seed,'stages':selected,
            'per_stage':results,'summary':{'layer_comparison':summary},
            'interpretation_constraints':[
                'Primary inductive evaluation uses NO held-out game activations for centering or model fitting.',
                'Unlabeled-context-centered evaluation uses the full held-out unlabeled batch for normalization and is TRANSDUCTIVE, not deployable one-at-a-time.',
                'No held-out number labels or held-out game labels are fitted in either condition.',
                'Projection rank, ridge strength and kernel hyperparameters are identical across stages; achieved rank can differ.',
                'Shuffled-label null re-fits the entire supervised basis, scaling and decoder on shuffled train-only labels.',
                'Success could reflect shared lexical/tokenization representations, not proven numerical magnitude or arithmetic.',
                'Synthetic radius substitutions are not certified game answers; zero MAE is decoder error, not game zero accuracy.',
                'A failed decoder does not prove information was deleted.'],
            'artifacts':['final_report.json','double_holdout_predictions.npz']}
    (dest/'final_report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print('GAMER RADIUS DOUBLE-HOLDOUT COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return report


def main(argv=None):
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--radius-run',required=True,type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--folds',type=int,default=4)
    ap.add_argument('--rank',type=int,default=12)
    ap.add_argument('--controls',type=int,default=1)
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--stages',default=None,help='Comma-separated captured stages; default is 12 comparison stages')
    args=ap.parse_args(argv)
    names=args.stages.split(',') if args.stages else None
    return run_double_holdout(args.radius_run,output=args.output,folds=args.folds,
                              rank=args.rank,controls=args.controls,seed=args.seed,stages=names)

if __name__=='__main__':main()
