#!/usr/bin/env python3
"""Read-only, contrastive shared-information microscope for GAMER V1.

Unlike the earlier single-direction probe, this discovers a low-dimensional
*equivalence code* for activation changes: two interventions are positive
matches only if they represent the same numerical displacement across
DIFFERENT games; nonmatches use a different displacement in the same game.
No scalar answer or numerical magnitude is regressed during discovery.

A strict evaluation changes the game, numeral range, and (for the main arm)
spelling. Training only sees outer-range number literals; test only sees
inner-range literals. An independent random-label fit is the negative control.
The reported held-out predictive information is an out-of-sample log-loss
improvement over a uniform three-class prior, not proof of semantic meaning.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_radius_activations as radius
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.shared-information.v1'
LABELS=(1,2,3)
DEFAULT_STAGES=('embedding_mean100','qwen_layer_07_last','qwen_layer_07_mean100',
 'qwen_layer_14_mean100','qwen_layer_21_mean100','qwen_final_last',
 'qwen_final_mean100','qwen_final_100','c0_input_norm','c0_hidden_1',
 'c0_hidden_2','c0_hidden_3','c0_bottleneck','c0_answer')
# Each arm fits on outer-range numbers, and tests on completely disjoint
# inner-range numerals in games absent from training.
ARMS={
 'new_games_new_values':(('digits','signed'),('positive','negative'),('digits','signed'),('positive','negative')),
 'new_games_values_words':(('digits','signed'),('positive','negative'),('words',),('positive','negative')),
 'positive_digits_to_negative_words':(('digits','signed'),('positive',),('words',),('negative',)),
 'negative_digits_to_positive_words':(('digits','signed'),('negative',),('words',),('positive',)),
 'unseen_zero_crossing_words':(('digits','signed'),('positive','negative'),('words',),('positive',)),
}


def _sample_sketch(arr,*,dim=256,seed=79):
    """Fixed, label-free CountSketch of *all* input dimensions.

    Uses one signed bucket per coordinate, not a slice of the 102400D input.
    No held-out data or numerical labels are used to fit this projection.
    """
    from scipy import sparse
    x=np.asarray(arr,dtype=np.float32)
    if not np.isfinite(x).all():raise ValueError('GAMER_SHARED_NONFINITE_ACTIVATIONS')
    width=x.shape[-1]
    if width<=dim:return x.copy(),{'type':'identity','source_dims':width,'output_dims':width}
    rng=np.random.default_rng(seed)
    buckets=rng.integers(dim,size=width,dtype=np.int32)
    signs=rng.choice(np.array([-1.,1.],dtype=np.float32),size=width)
    proj=sparse.csr_matrix((signs,(np.arange(width),buckets)),shape=(width,dim),dtype=np.float32)
    out=(x.reshape(-1,width)@proj).reshape(x.shape[:-1]+(dim,)).astype(np.float32)
    return out,{'type':'fixed_countsketch','source_dims':width,'output_dims':dim,
                'seed':seed,'collisions_possible':True}


def _folds(games,folds=4,seed=47):
    if len(games)<folds*2 or len(set(games))!=len(games):
        raise ValueError('GAMER_SHARED_INSUFFICIENT_UNIQUE_GAMES')
    return distance._group_folds(games,folds=folds,seed=seed)


def _rows(a,counts,values,games_ix,forms,signs,region,*,zero_cross=False):
    """Features, relation *identities*, and provenance; no fitted regression.

    Training values in outer region [-8..-5] / [5..8], testing in inner
    region [-4..-1] / [1..4]. Zero crossing is separately identified and
    never used in training. Token-length mismatches are not silently dropped.
    """
    v={int(n):i for i,n in enumerate(values)}
    if not all(k in v for k in range(-8,9)):
        raise ValueError('GAMER_SHARED_REQUIRES_RADIUS_8')
    x=[];y=[];meta=[]
    for game in games_ix:
        for form in forms:
            fi=distance.FORMS.index(form)
            for sign in signs:
                if zero_cross:
                    candidates=[(start,start+d,d) for d in LABELS for start in range(-d,1)
                               if start in v and start+d in v and start<=0<=start+d]
                    # Include interventions that genuinely pass or touch zero.
                else:
                    bounds=((-8,-5) if region=='outer' else (-4,-1)) if sign=='negative' else (
                           (5,8) if region=='outer' else (1,4))
                    candidates=[(start,start+d,d) for d in LABELS
                                for start in range(bounds[0],bounds[1]+1-d)]
                for start,end,d in candidates:
                    i,j=v[start],v[end]
                    x.append(a[game,fi,j]-a[game,fi,i]);y.append(d)
                    meta.append({'game':int(game),'form':form,'sign':sign,
                                 'start':start,'end':end,'delta':d,
                                 'token_count_changed':bool(counts[game,fi,i]!=counts[game,fi,j])})
    if not x:raise ValueError('GAMER_SHARED_EMPTY_RELATION_ROWS')
    return np.stack(x).astype(np.float32),np.asarray(y,dtype=np.int64),meta


def _invariant_features(x):
    # Rotation-invariant magnitudes are computed BEFORE train-set scaling.
    # These can expose shared numerical distance on a curved manifold even
    # when equal steps have unrelated or opposite activation directions.
    a=np.asarray(x,dtype=np.float64)
    rms=np.sqrt(np.mean(a*a,axis=1))
    meanabs=np.mean(np.abs(a),axis=1)
    peak=np.max(np.abs(a),axis=1)
    return np.column_stack((np.log1p(rms),np.log1p(meanabs),np.log1p(peak))).astype(np.float32)


def _scale(train,test):
    # Only training observations can determine the coordinate scaling.
    train=np.concatenate((train,_invariant_features(train)),axis=1)
    test=np.concatenate((test,_invariant_features(test)),axis=1)
    mean=np.mean(train,axis=0,dtype=np.float64).astype(np.float32)
    sd=np.std(train,axis=0,dtype=np.float64).astype(np.float32)
    sd=np.maximum(sd,0.05*np.max(sd) if sd.size else 1.,dtype=np.float32)
    sd=np.maximum(sd,1e-5)
    return np.clip((train-mean)/sd,-25,25),np.clip((test-mean)/sd,-25,25)


def _permuted_labels(y,meta,seed):
    """Shuffle relation identities WITHIN each game/form/sign cell."""
    rng=np.random.default_rng(seed);result=y.copy()
    by_group={}
    for i,m in enumerate(meta):by_group.setdefault((m['game'],m['form'],m['sign']),[]).append(i)
    for indices in by_group.values():
        ii=np.asarray(indices,dtype=np.int64)
        result[ii]=rng.permutation(y[ii])
    return result


def _triplets(labels,meta,*,samples=320,seed=47):
    """Positive same relation across DIFFERENT games; negative different
    relation with identical game/form/sign to suppress nuisance shortcuts."""
    rng=np.random.default_rng(seed)
    by_label={d:np.where(labels==d)[0] for d in LABELS}
    by_nuisance={}
    for i,m in enumerate(meta):by_nuisance.setdefault((m['game'],m['form'],m['sign']),[]).append(i)
    by_nuisance={k:np.asarray(v,dtype=np.int64) for k,v in by_nuisance.items()}
    a=[];p=[];n=[]
    for i in range(samples*4):
        d=LABELS[i%len(LABELS)]
        if not len(by_label[d]):continue
        anchor=int(rng.choice(by_label[d]));m=meta[anchor]
        choices=[j for j in by_label[d] if meta[j]['game']!=m['game'] and
                 (meta[j]['form']!=m['form'] or meta[j]['sign']!=m['sign'])]
        if not choices:choices=[j for j in by_label[d] if meta[j]['game']!=m['game']]
        negatives=[j for j in by_nuisance[(m['game'],m['form'],m['sign'])] if labels[j]!=d]
        if not choices or not negatives:continue
        a.append(anchor);p.append(int(rng.choice(choices)));n.append(int(rng.choice(negatives)))
        if len(a)>=samples:break
    if len(a)<min(32,samples):raise ValueError('GAMER_SHARED_NOT_ENOUGH_CROSS_VIEW_MATCHES')
    return np.asarray(a),np.asarray(p),np.asarray(n)


def _fit_relation_code(train,labels,meta,*,seed=47,steps=90,code_width=16):
    """Fit a *matching* code, not a scalar numerical regression."""
    import torch
    torch.manual_seed(seed)
    torch.set_num_threads(min(torch.get_num_threads(),4))
    din=train.shape[-1]
    net=torch.nn.Sequential(torch.nn.Linear(din,48),torch.nn.Tanh(),
                            torch.nn.Linear(48,code_width))
    opt=torch.optim.AdamW(net.parameters(),lr=0.008,weight_decay=0.001)
    x=torch.from_numpy(train.astype(np.float32))
    ai,pi,ni=_triplets(labels,meta,seed=seed,samples=320)
    ai=torch.from_numpy(ai);pi=torch.from_numpy(pi);ni=torch.from_numpy(ni)
    net.train()
    for _ in range(steps):
        z=net(x)
        dp=torch.sum((z[ai]-z[pi])**2,dim=1)
        dn=torch.sum((z[ai]-z[ni])**2,dim=1)
        loss=dp.mean()+torch.relu(1.0-torch.sqrt(dn+1e-10)).square().mean()*2.0
        loss=loss+0.0005*z.square().mean()
        opt.zero_grad(set_to_none=True);loss.backward();opt.step()
    net.eval()
    return net


def _evaluate_code(net,train,test,labels_tr,labels_te,*,meta_te=None,seed=47):
    import torch
    with torch.no_grad():
        ztr=net(torch.from_numpy(train.astype(np.float32))).numpy()
        zte=net(torch.from_numpy(test.astype(np.float32))).numpy()
    cents=np.stack([np.mean(ztr[labels_tr==d],axis=0) for d in LABELS])
    # Temperature is determined by training data, never tuned on held-out games.
    train_dist=np.sum((ztr-cents[labels_tr-1])**2,axis=-1)
    tau=max(float(np.median(train_dist)),0.05)
    dist=np.sum((zte[:,None,:]-cents[None,:,:])**2,axis=-1)
    logits=-dist/tau
    logits=logits-np.max(logits,axis=1,keepdims=True)
    prob=np.exp(logits);prob=prob/np.sum(prob,axis=1,keepdims=True)
    yp=np.argmax(prob,axis=1)+1
    logloss=-np.log(np.maximum(prob[np.arange(len(labels_te)),labels_te-1],1e-12))
    correct=yp==labels_te
    # The available start/end pairs occur in a 3:2:1 class ratio. Evaluate
    # on a uniform relation prior so majority-class predictions cannot create
    # spurious information. This is a BALANCED held-out log-score.
    ce=float(np.mean([np.mean(logloss[labels_te==d]) for d in LABELS]))
    balanced_accuracy=float(np.mean([np.mean(correct[labels_te==d]) for d in LABELS]))
    bits=float(math.log2(len(LABELS))-ce/math.log(2))
    # Conditional held-out null: shuffle matching labels WITHIN each test
    # game, preserving game effects and the empirical relation frequencies.
    permutation_bits=[]
    if meta_te:
        groups={}
        for i,m in enumerate(meta_te):groups.setdefault(m['game'],[]).append(i)
        rng=np.random.default_rng(seed+999)
        for j in range(99):
            fake=labels_te.copy()
            for ids in groups.values():
                ix=np.asarray(ids,dtype=np.int64)
                fake[ix]=rng.permutation(labels_te[ix])
            ll=-np.log(np.maximum(prob[np.arange(len(fake)),fake-1],1e-12))
            fake_ce=float(np.mean([np.mean(ll[fake==d]) for d in LABELS]))
            permutation_bits.append(float(math.log2(len(LABELS))-fake_ce/math.log(2)))
    result={'cases':int(len(labels_te)),'match_accuracy':balanced_accuracy,
            'raw_match_accuracy':float(np.mean(correct)),
            'chance_accuracy':1/len(LABELS),'heldout_predictive_information_bits':bits,
            'heldout_cross_entropy_bits':float(ce/math.log(2)),
            'predicted_distribution':{str(d):int(np.sum(yp==d)) for d in LABELS},
            'per_delta':{str(d):{'cases':int(np.sum(labels_te==d)),
                       'accuracy':float(np.mean(correct[labels_te==d]))} for d in LABELS},
            'token_count_changed_cases':int(sum(m['token_count_changed'] for m in meta_te)) if meta_te else None,
            'heldout_within_game_permutation_bits':permutation_bits}
    return result


def _zero_spelling_metrics(a,values,minus_zero=None):
    i=np.where(values==0)[0]
    if len(i)!=1:return {'status':'NO_ZERO'}
    ix=int(i[0]);ref=a[:,0,ix,:]
    one=np.linalg.norm(a[:,0,np.where(values==1)[0][0],:]-ref,axis=1)
    out={}
    for k,form in ((1,'signed_zero'),(2,'word_zero')):
        distances=np.linalg.norm(a[:,k,ix,:]-ref,axis=1)
        ratios=distances/np.maximum(one,1e-10)
        out[form]={'median_distance_over_plus_one_step':float(np.median(ratios)),
                   'cases':int(len(ratios))}
    if minus_zero is not None:
        distances=np.linalg.norm(minus_zero-ref,axis=1)
        out['minus_zero']={'median_distance_over_plus_one_step':float(np.median(distances/np.maximum(one,1e-10))),
                           'cases':int(len(distances))}
    return out


def _combine(results):
    if not results:return {'status':'NO_EVALUATIONS'}
    total=sum(r['cases'] for r in results)
    bits=float(sum(r['cases']*r['heldout_predictive_information_bits'] for r in results)/total)
    if results[0].get('heldout_within_game_permutation_bits'):
        nulls=np.asarray([r['heldout_within_game_permutation_bits'] for r in results])
        weighted=np.average(nulls,axis=0,weights=[r['cases'] for r in results])
        p=float((1+np.sum(weighted>=bits))/(1+len(weighted)))
    else:p=None
    return {'status':'COMPLETE','cases':total,
            'match_accuracy':float(sum(r['cases']*r['match_accuracy'] for r in results)/total),
            'chance_accuracy':1/len(LABELS),
            'heldout_predictive_information_bits':bits,
            'heldout_within_game_permutation_p':p,
            'token_count_changed_cases':int(sum(r['token_count_changed_cases'] or 0 for r in results))}


def analyze_shared(a,counts,values,games,*,folds=4,seed=47,steps=90,controls=1,sketch_dim=256,arms=None,minus_zero=None):
    """Return out-of-game, out-of-number, cross-spelling information tests."""
    x=np.asarray(a,dtype=np.float32);counts=np.asarray(counts);values=np.asarray(values,dtype=int)
    if x.ndim!=4 or x.shape[:3]!=(len(games),len(distance.FORMS),len(values)) or counts.shape!=x.shape[:3]:
        raise ValueError('GAMER_SHARED_BAD_CAPTURE_SHAPE')
    if not np.isfinite(x).all():raise ValueError('GAMER_SHARED_NONFINITE_ACTIVATIONS')
    if steps<1 or steps>1000 or controls<0 or controls>8 or sketch_dim<8 or sketch_dim>1024:
        raise ValueError('GAMER_SHARED_BAD_BUDGET')
    if not all(k in values for k in range(-8,9)):
        raise ValueError('GAMER_SHARED_REQUIRES_RADIUS_8')
    sketch,projection=_sample_sketch(x,dim=sketch_dim,seed=seed+91)
    partitions=_folds(games,folds=folds,seed=seed)
    arms=ARMS if arms is None else {k:ARMS[k] for k in arms}
    minus_sketch=None
    if minus_zero is not None:
        minus_sketch,_=_sample_sketch(np.asarray(minus_zero,dtype=np.float32),dim=sketch_dim,seed=seed+91)
    result={'projection':projection,'numeric_fit':'RELATION_EQUALITY_ONLY_NO_SCALAR_REGRESSION',
            'zero_spellings':_zero_spelling_metrics(sketch,values,minus_sketch),'arms':{}}
    for arm,(train_forms,train_signs,test_forms,test_signs) in arms.items():
        results=[];nulls=[[] for _ in range(controls)]
        for fold_id,(tr,te) in enumerate(partitions):
            xtr,ytr,mtr=_rows(sketch,counts,values,tr,train_forms,train_signs,'outer')
            xte,yte,mte=_rows(sketch,counts,values,te,test_forms,test_signs,'inner',
                              zero_cross=(arm=='unseen_zero_crossing_words'))
            sx,st=_scale(xtr,xte)
            model=_fit_relation_code(sx,ytr,mtr,seed=seed+fold_id*97,steps=steps)
            results.append(_evaluate_code(model,sx,st,ytr,yte,meta_te=mte,seed=seed+fold_id*97))
            for j in range(controls):
                ys=_permuted_labels(ytr,mtr,seed+fold_id*733+j*991+123)
                null_model=_fit_relation_code(sx,ys,mtr,seed=seed+fold_id*97+j*311+123,steps=steps)
                nulls[j].append(_evaluate_code(null_model,sx,st,ys,yte,meta_te=mte,seed=seed+fold_id*97))
        combined=_combine(results)
        null_scores=[_combine(n)['heldout_predictive_information_bits'] for n in nulls]
        # Candidate only, never semantic proof: all three tests must pass,
        # and a shuffled TRAINING-match fit must not perform as well.
        combined['shared_relation_candidate']=(
            combined['heldout_predictive_information_bits']>0.05 and
            combined['match_accuracy']>1/len(LABELS)+0.08 and
            combined['heldout_within_game_permutation_p'] is not None and
            combined['heldout_within_game_permutation_p']<=0.05 and
            (not null_scores or combined['heldout_predictive_information_bits']>max(null_scores)))
        combined.update({'folds':folds,'train_range':'outer -8..-5 and +5..+8',
           'test_range':'inner -4..-1 and +1..+4',
           'train_forms':train_forms,'test_forms':test_forms,
           'train_signs':train_signs,'test_signs':test_signs,
           'random_match_controls_bits':null_scores,
           'random_match_control_max_bits':max(null_scores) if null_scores else None,
           'empirical_p_upper_bound':float((1+sum(v>=combined['heldout_predictive_information_bits'] for v in null_scores))/(1+len(null_scores))) if null_scores else None,
           'fold_details':results})
        result['arms'][arm]=combined
    return result


def _destination(source,output):
    return distance._destination(source,output,'-shared-information')


def run_shared(capture,*,output=None,stages=None,folds=4,steps=90,controls=1,seed=47,sketch_dim=256):
    """Offline only: no CUDA, LoRA, head weights, or optimizer."""
    src=Path(capture).resolve()
    files=[src/'final_report.json',src/'activations.npz',src/'interventions.jsonl']
    for f in files:
        if not f.is_file():raise FileNotFoundError(f)
    report=json.loads(files[0].read_text(encoding='utf-8'))
    if report.get('schema')!=distance.SCHEMA or report.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_SHARED_EXPECTS_NUMBER_DISTANCE_CAPTURE')
    names=tuple(stages or DEFAULT_STAGES)
    if not names or len(set(names))!=len(names) or not set(names).issubset(distance.STAGES):
        raise ValueError('GAMER_SHARED_INVALID_STAGES')
    games=[c['game_id'] for c in report['contexts']]
    if folds<2 or folds>min(12,len(games)//2):raise ValueError('GAMER_SHARED_INVALID_FOLDS')
    dest=_destination(src,output)
    for forbidden in (src,report['expanded_run'],report['source_run']):
        root=Path(forbidden).resolve()
        if dest==root or dest in root.parents or root in dest.parents:
            raise ValueError('GAMER_SHARED_OUTPUT_OVERLAPS_SOURCE')
    sha={p.name:radius._canonical_hash(p) for p in files}
    stages_out={};ranked=[]
    with np.load(files[1],allow_pickle=False) as npz:
        values=npz['values'];counts=npz['token_counts']
        for i,stage in enumerate(names):
            if stage not in npz.files:raise ValueError('GAMER_SHARED_CAPTURE_STAGE_MISSING:'+stage)
            result=analyze_shared(npz[stage],counts,values,games,folds=folds,
                     seed=seed,steps=steps,controls=controls,sketch_dim=sketch_dim,
                     minus_zero=(npz['minus_zero__'+stage] if 'minus_zero__'+stage in npz.files else None))
            stages_out[stage]=result
            strict=result['arms']['new_games_values_words']
            ranked.append({'stage':stage,'strict_cross_form_accuracy':strict['match_accuracy'],
               'strict_cross_form_information_bits':strict['heldout_predictive_information_bits'],
               'shuffled_control_max_bits':strict['random_match_control_max_bits']})
            print(f'  {stage}: heldout_words_acc={strict["match_accuracy"]:.3f}'
              f' shared_info_bits={strict["heldout_predictive_information_bits"]:+.4f}'
              f' shuffled_max={strict["random_match_control_max_bits"]}',flush=True)
    for p in files:
        if radius._canonical_hash(p)!=sha[p.name]:raise ValueError('GAMER_SHARED_SOURCE_CHANGED')
    dest.mkdir(parents=True,exist_ok=False)
    result={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY','source_capture':str(src),
       'capture_sha256':sha,'checkpoint_step':report.get('checkpoint_step'),
       'games':len(games),'folds':folds,'steps_per_fit':steps,'controls':controls,
       'sketch_dim':sketch_dim,'seed':seed,'relation_classes':LABELS,
       'by_stage':stages_out,
       'shared_across_games_forms_signs_candidates':[stage for stage,r in stages_out.items()
          if all(r['arms'][key]['shared_relation_candidate'] for key in (
             'new_games_values_words','positive_digits_to_negative_words',
             'negative_digits_to_positive_words'))],
       'ranking_by_cross_form_information':sorted(ranked,key=lambda k:k['strict_cross_form_information_bits'],reverse=True),
       'interpretation_constraints':[
         'Matched interventions label EQUALITY of numerical displacements; this probe does not regress numerical answers or magnitudes.',
         'The learned matching probe is trained on relation labels; only held-out cross-context and spelling transfer can support a shared-information claim.',
         'Outer and inner numeric literal sets are disjoint; games are also held out; the primary arm holds out all word spellings.',
         'CountSketch is a fixed, label-blind approximation and can obscure information; failed scores do not establish information absence.',
         'Reported predictive-information bits are an out-of-sample cross-entropy improvement over uniform guessing, not an exact mutual-information estimate.',
         'A held-out within-game permutation test (99 permutations) and separately re-fitted random-match controls test different null hypotheses.',
         'Passing gates yields a candidate shared relation code, not a verified canonical numerical representation; the probe is supervised on equality labels.',
         'Changing numeral length can change 100-vector sampling; token-count mismatches are included and counted, not assumed harmless.',
         'Cross-zero numeric relations and zero spellings require special controls; this report does not certify zero-line arithmetic.',
         'No source checkpoint or capture is modified; no LoRA or C0 training occurs.'
       ]}
    gamer.atomic_json(dest/'final_report.json',result)
    print('GAMER SHARED INFORMATION COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--stages',default=None)
    p.add_argument('--folds',type=int,default=4)
    p.add_argument('--steps',type=int,default=90)
    p.add_argument('--controls',type=int,default=1)
    p.add_argument('--seed',type=int,default=47)
    p.add_argument('--sketch-dim',type=int,default=256)
    a=p.parse_args()
    run_shared(a.run,output=a.output,
       stages=tuple(x.strip() for x in a.stages.split(',')) if a.stages else None,
       folds=a.folds,steps=a.steps,controls=a.controls,seed=a.seed,
       sketch_dim=a.sketch_dim)
