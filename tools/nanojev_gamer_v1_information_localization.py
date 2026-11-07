#!/usr/bin/env python3
"""Read-only, uncompressed localization of reproducible numerical-distance geometry.

This probe discovers *where* activation changes preserve numerical-distance
ordering. It does not train a representation or regress values. It scores each
of the original 100 x 1024 input blocks independently, selects block(s) using
training games/outer numeric values, then tests unseen games, inner numeric
values and words. A nested 64-coordinate tile selection localizes candidates
within each selected block. Norm-based ordering is invariant to rotations of
activation coordinates but is not a complete information-theoretic test.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_radius_activations as radius
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA = 'nanojev.gamer.v1.information-localization.v1'
DEFAULT_STAGES = ('qwen_final_100', 'c0_input_norm', 'qwen_final_mean100',
                  'c0_hidden_1', 'c0_bottleneck')
# For strictly held-out tests, all training numbers occur in outer [-8,-5]
# and [5,8], while every testing number is in inner [-4,-1], [1,4], or
# zero-crossing [-3,3]. Whole games are disjoint across train and test.
ARMS = {
    'unseen_games_values_digits': (('digits','signed'), ('positive','negative'),
                                 ('digits','signed'), ('positive','negative'), False),
    'unseen_games_values_words': (('digits','signed'), ('positive','negative'),
                                ('words',), ('positive','negative'), False),
    'negative_digits_to_positive_words': (('digits','signed'), ('negative',),
                                          ('words',), ('positive',), False),
    'positive_digits_to_negative_words': (('digits','signed'), ('positive',),
                                          ('words',), ('negative',), False),
    'unseen_zero_crossing_words': (('digits','signed'), ('positive','negative'),
                                   ('words',), ('positive','negative'), True),
}


def _layout(width, tile_width=64):
    """Preserve actual 100x1024 vector boundaries; otherwise group head units."""
    if width == 102400:
        return 100,1024,'sampled_qwen_token'
    if width >= 1024 and width % 1024 == 0:
        return width//1024,1024,'activation_block_not_token'
    return 1,width,'activation_block_not_token'


def _pairs(values, region, sign, *, zero=False):
    idx={int(n):i for i,n in enumerate(values)}
    if zero:
        # Unequal tokenizer lengths are deliberately included in this arm.
        candidates=[(a,a+d,d) for d in (1,2,3) for a in range(-3,1)
                    if a<=0<=a+d and a<a+d and a in idx and a+d in idx]
    else:
        lo,hi=((-8,-5) if region=='outer' else (-4,-1)) if sign=='negative' else (
              (5,8) if region=='outer' else (1,4))
        candidates=[(a,a+d,d) for d in (1,2,3)
                    for a in range(lo,hi+1-d) if a in idx and a+d in idx]
    return [(idx[a],idx[b],d,a,b) for a,b,d in candidates]


def _records(a, counts, values, game_indices, forms, signs, region, *, zero=False,
             selected_blocks=None, subtiles=False, tile_width=64, cache=None):
    """Build one within-game/form/sign ranking task per group.

    Input a: (games, 3 forms, numeric values, dimensions). Return a list of
    (pair-labels, Euclidean distances for each tested block/tile, provenance).
    Uses no fitted coordinate projection or activation normalization.
    """
    ng,block_dim,kind=_layout(a.shape[-1])
    blocks=list(range(ng)) if selected_blocks is None else list(selected_blocks)
    result=[]
    for game in game_indices:
        for form in forms:
            fi=distance.FORMS.index(form)
            # Zero-crossing relations have no positive/negative arm identity.
            # Evaluate them once, not twice with duplicate score evidence.
            for sign in (('cross_zero',) if zero else signs):
                key=(int(game),form,sign,region,bool(zero),tuple(blocks),bool(subtiles),int(tile_width))
                if cache is not None and key in cache:
                    result.append(cache[key]);continue
                pairs=_pairs(values,region,sign,zero=zero)
                if len(pairs)<3:continue
                ds=[];changes=0
                for ix,jx,d,_,_ in pairs:
                    # Reshape without assigning a fake positional meaning to
                    # non-100-vector stages. npz is loaded one stage at a time.
                    delta=np.asarray(a[game,fi,jx]-a[game,fi,ix],dtype=np.float32).reshape(ng,block_dim)
                    if subtiles:
                        if len(blocks)!=1:raise ValueError('GAMER_LOCALIZE_TILE_REQUIRES_ONE_BLOCK')
                        feat=delta[blocks[0]]
                        # Each tile is contiguous; the final tile may be short.
                        item=np.asarray([np.linalg.norm(feat[k:k+tile_width].astype(np.float64))
                           for k in range(0,block_dim,tile_width)],dtype=np.float64)
                    else:
                        item=np.linalg.norm(delta[blocks].astype(np.float64),axis=1)
                    ds.append(item)
                    changes+=int(counts[game,fi,ix]!=counts[game,fi,jx])
                record=(np.asarray([x[2] for x in pairs],dtype=np.int8),
                    np.asarray(ds,dtype=np.float64),
                    {'game':int(game),'form':form,'sign':sign,
                     'pair_count':len(pairs),'token_count_mismatched_pairs':changes})
                if cache is not None:cache[key]=record
                result.append(record)
    return result,blocks,kind


def _compare_matrix(y):
    # For all ordered pairs where target Δ differs, mark whether second is larger.
    i,j=np.where(y[:,None]<y[None,:])
    return i,j


def _group_scores(rows, *, permute=False, rng=None):
    """Fraction of larger numerical displacements with larger feature norms.

    Chance = .5, ties = .5. Every game/form/sign group receives equal weight,
    rather than giving highly replicated numeric pairs disproportionate weight.
    """
    scores=[]
    for y,dist,_ in rows:
        if permute:y=rng.permutation(y)
        i,j=_compare_matrix(y)
        if not len(i):continue
        diff=dist[j]-dist[i]
        scores.append(np.mean((diff>1e-9).astype(np.float64)+0.5*(np.abs(diff)<=1e-9),axis=0))
    if not scores:raise ValueError('GAMER_LOCALIZE_NO_DISTANCE_COMPARISONS')
    return np.asarray(scores,dtype=np.float64)


def _aggregate(scores):
    return np.mean(scores,axis=0)


def _rank_blocks(train, k):
    means=_aggregate(_group_scores(train))
    return np.argsort(-means,kind='stable')[:min(k,len(means))].tolist(),means


def _folds(games,n=4,seed=47):
    return distance._group_folds(games,folds=n,seed=seed)


def _token_count_baseline(counts,values,game_indices,forms,signs,region,*,zero=False):
    # Label-independent control: can changes in the number of tokenizer tokens
    # alone predict the numeric distance within the heldout conditions?
    records=[]
    for game in game_indices:
        for form in forms:
            fi=distance.FORMS.index(form)
            for sign in (('cross_zero',) if zero else signs):
                pairs=_pairs(values,region,sign,zero=zero)
                if len(pairs)<3:continue
                y=np.asarray([p[2] for p in pairs],dtype=np.int8)
                d=np.asarray([abs(int(counts[game,fi,j])-int(counts[game,fi,i]))
                              for i,j,_,_,_ in pairs],dtype=np.float64)[:,None]
                records.append((y,d,{'game':int(game),'form':form,'sign':sign}))
    return float(np.mean(_group_scores(records))) if records else None


def _zero_spellings(a,values,games,block_indices,minus_zero=None):
    idx={int(x):i for i,x in enumerate(values)}
    ng,bs,kind=_layout(a.shape[-1]);out={}
    if 0 not in idx or 1 not in idx:return {'status':'NO_ZERO'}
    blocks=sorted(set(block_indices))
    for name,form in (('plus_zero','signed'),('word_zero','words'),('minus_zero','minus_zero')):
        if form=='minus_zero' and minus_zero is None:continue
        output=[]
        for game in games:
            ref=np.asarray(a[game,0,idx[0]],dtype=np.float64).reshape(ng,bs)[blocks]
            step=np.asarray(a[game,0,idx[1]],dtype=np.float64).reshape(ng,bs)[blocks]-ref
            alt=(np.asarray(minus_zero[game],dtype=np.float64).reshape(ng,bs)[blocks]
                 if form=='minus_zero' else
                 np.asarray(a[game,distance.FORMS.index(form),idx[0]],dtype=np.float64).reshape(ng,bs)[blocks])
            num=np.linalg.norm(alt-ref,axis=1);den=np.linalg.norm(step,axis=1)
            output.append(num/np.maximum(den,1e-10))
        r=np.asarray(output)
        out[name]={str(block):{'median_ratio_to_digit_zero_to_one':float(np.median(r[:,i])),
                     'cases':len(games)} for i,block in enumerate(blocks)}
    return {'status':'COMPLETE','by_spelling':out,
            'ratios_are_descriptive_not_shared_information_tests':True}


def analyze_localization(a,counts,values,games,*,folds=4,seed=47,top=8,
                         tile_width=64,permutations=99,arms=None,minus_zero=None):
    """Train-only localization; never train or fit a numeric readout.

    The held-out null permutes numerical-distance labels *within* each test
    game/form/sign group. The candidate location is fixed by training games,
    so the null includes no selection on held-out outcomes.
    """
    a=np.asarray(a,dtype=np.float32);counts=np.asarray(counts)
    values=np.asarray(values,dtype=np.int32)
    if a.ndim!=4 or a.shape[:3]!=(len(games),len(distance.FORMS),len(values)) or counts.shape!=a.shape[:3]:
        raise ValueError('GAMER_LOCALIZE_BAD_CAPTURE_SHAPE')
    if not np.isfinite(a).all() or not all(k in values for k in range(-8,9)):
        raise ValueError('GAMER_LOCALIZE_BAD_CAPTURE_VALUES')
    if not 2<=folds<=len(games)//2 or not 1<=top<=100 or not 1<=tile_width<=1024 or not 0<=permutations<=999:
        raise ValueError('GAMER_LOCALIZE_BAD_BUDGET')
    ng,block_dim,kind=_layout(a.shape[-1]);fold_groups=_folds(games,n=folds,seed=seed)
    options=ARMS if arms is None else {s:ARMS[s] for s in arms}
    cache={}  # Cache raw, complete block distances for repeated folds and arms.
    result={'kind':kind,'block_count':ng,'dimensions_per_block':block_dim,
            'subtile_width':tile_width,'blocks':{},'arms':{}}
    for arm_index,(arm,(train_forms,train_signs,test_forms,test_signs,zero)) in enumerate(options.items()):
        fold_details=[];nulls=[];selected_counts=np.zeros(ng,dtype=int)
        for fi,(tr,te) in enumerate(fold_groups):
            train,_,_=_records(a,counts,values,tr,train_forms,train_signs,'outer',cache=cache)
            selected,train_scores=_rank_blocks(train,top)
            for block in selected:selected_counts[block]+=1
            test,_,_=_records(a,counts,values,te,test_forms,test_signs,'inner',zero=zero,cache=cache)
            full_test=_group_scores(test)
            selected_test=full_test[:,selected]
            # Locked train-only top-1, and mean of train-only top-K.
            score=float(np.mean(selected_test[:,0]));portfolio=float(np.mean(selected_test))
            lexical_control=_token_count_baseline(counts,values,te,test_forms,test_signs,
                                                  'inner',zero=zero)
            # Nested training-only localization within the selected 1024D block.
            train_tiles,_,_=_records(a,counts,values,tr,train_forms,train_signs,'outer',
                                    selected_blocks=(selected[0],),subtiles=True,tile_width=tile_width,cache=cache)
            test_tiles,_,_=_records(a,counts,values,te,test_forms,test_signs,'inner',zero=zero,
                                   selected_blocks=(selected[0],),subtiles=True,tile_width=tile_width,cache=cache)
            tile_train=_aggregate(_group_scores(train_tiles))
            tile=int(np.argmax(tile_train))
            tile_score=float(np.mean(_group_scores(test_tiles)[:,tile]))
            fold_rng=np.random.default_rng(seed+arm_index*30011+fi*337)
            # Null selection fixed on train; only test labels are randomized.
            null_fold=[]
            for _ in range(permutations):
                null_fold.append(float(np.mean(_group_scores(test,permute=True,rng=fold_rng)[:,selected[0]])))
            nulls.append(null_fold)
            fold_details.append({'fold':fi,'train_games':len(tr),'test_games':len(te),
                 'train_groups':len(train),'test_groups':len(test),
                 'top_blocks':selected,'train_selected_scores':[float(train_scores[k]) for k in selected],
                 'heldout_top1_score':score,'heldout_topk_mean_score':portfolio,
                 'heldout_token_count_only_ordering':lexical_control,
                 'nested_top1_tile':tile,'nested_coordinate_range':[
                       selected[0]*block_dim+tile*tile_width,
                       selected[0]*block_dim+min((tile+1)*tile_width,block_dim)],
                 'nested_train_tile_score':float(tile_train[tile]),
                 'nested_heldout_tile_score':tile_score,
                 'token_count_changed_test_pairs':sum(r[2]['token_count_mismatched_pairs'] for r in test)})
        primary=float(np.mean([f['heldout_top1_score'] for f in fold_details]))
        portfolio=float(np.mean([f['heldout_topk_mean_score'] for f in fold_details]))
        tile=float(np.mean([f['nested_heldout_tile_score'] for f in fold_details]))
        lexical=float(np.mean([f['heldout_token_count_only_ordering'] for f in fold_details]))
        if permutations:
            perm_average=np.mean(np.asarray(nulls,dtype=np.float64),axis=0)
            p=float((1+np.sum(perm_average>=primary-1e-12))/(1+permutations))
            nullmax=float(np.max(perm_average))
        else:p=None;nullmax=None
        result['arms'][arm]={'status':'COMPLETE','chance_ordering_score':0.5,
            'heldout_top1_ordering':primary,'heldout_topk_mean_ordering':portfolio,
            'heldout_nested_tile_ordering':tile,'heldout_token_count_only_ordering':lexical,
            'permutation_p':p,
            'null_max_ordering':nullmax,
            'candidate_in_this_arm':bool(primary>=0.70 and primary>lexical+0.05 and p is not None and p<=0.01),
            'top_block_selection_frequency':{str(i):int(c) for i,c in enumerate(selected_counts) if c},
            'train_forms':train_forms,'test_forms':test_forms,
            'train_signs':train_signs,'test_signs':test_signs,
            'train_values':'outer -8..-5 and +5..+8',
            'test_values':'zero crossing -3..+3' if zero else 'inner -4..-1 and +1..+4',
            'fold_details':fold_details}
        print(f'    {arm}: top1={primary:.3f} topK={portfolio:.3f} tile={tile:.3f} p={p}',flush=True)
    # Most selected blocks in strict word-form arm are prime *locations* for
    # independent confirmation, not automatically validated information codes.
    main=result['arms'].get('unseen_games_values_words')
    if main:
        blocks=sorted(((int(k),v) for k,v in main['top_block_selection_frequency'].items()),
                      key=lambda kv:(-kv[1],kv[0]))
        selected=[i for i,_ in blocks[:top]]
    else:selected=[]
    result['zero_spelling_distances_on_selected_blocks']=_zero_spellings(a,values,
         list(range(len(games))),selected,minus_zero)
    result['primary_candidate']=bool(main and main['candidate_in_this_arm'])
    return result


def _destination(capture,output):
    return distance._destination(capture,output,'-information-localization')


def run_localization(capture,*,output=None,stages=None,folds=4,seed=47,
                     top=8,tile_width=64,permutations=99):
    """Offline-only analysis; verify original capture bytes stay unchanged."""
    src=Path(capture).resolve()
    files=[src/'final_report.json',src/'activations.npz',src/'interventions.jsonl']
    for path in files:
        if not path.is_file():raise FileNotFoundError(path)
    report=json.loads(files[0].read_text(encoding='utf-8'))
    if report.get('schema')!=distance.SCHEMA or report.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_LOCALIZE_EXPECTS_NUMBER_DISTANCE_CAPTURE')
    names=tuple(stages or DEFAULT_STAGES)
    if not names or len(set(names))!=len(names) or not set(names).issubset(distance.STAGES):
        raise ValueError('GAMER_LOCALIZE_BAD_STAGES')
    games=[p['game_id'] for p in report['contexts']]
    dest=_destination(src,output)
    for protected in (src,report['expanded_run'],report['source_run']):
        root=Path(protected).resolve()
        if dest==root or dest in root.parents or root in dest.parents:
            raise ValueError('GAMER_LOCALIZE_OUTPUT_OVERLAPS_SOURCE')
    if dest.exists():raise FileExistsError(dest)
    hashes={p.name:radius._canonical_hash(p) for p in files}
    by_stage={}
    with np.load(files[1],allow_pickle=False) as npz:
        values=npz['values'];counts=npz['token_counts']
        for stage in names:
            if stage not in npz.files:raise ValueError('GAMER_LOCALIZE_CAPTURE_STAGE_MISSING:'+stage)
            print(f'  stage {stage}',flush=True)
            by_stage[stage]=analyze_localization(npz[stage],counts,values,games,
                folds=folds,seed=seed,top=top,tile_width=tile_width,permutations=permutations,
                minus_zero=(npz['minus_zero__'+stage] if 'minus_zero__'+stage in npz.files else None))
    for p in files:
        if radius._canonical_hash(p)!=hashes[p.name]:raise ValueError('GAMER_LOCALIZE_SOURCE_CHANGED')
    result={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY','source_capture':str(src),
       'source_sha256':hashes,'games':len(games),'stages':names,'folds':folds,
       'seed':seed,'top':top,'tile_width':tile_width,'permutations':permutations,
       'by_stage':by_stage,
       'primary_cross_form_candidates':[k for k,v in by_stage.items() if v['primary_candidate']],
       'interpretation_constraints':[
          'No regression, contrastive neural network, random projection, or numeric head training occurs.',
          'Scores measure distance-ordering of raw activations within a game, not exact mutual information.',
          'Candidate blocks are selected only on training games with outer numeric values; whole heldout games, inner values, and words test transfer.',
          'Each reported 64D tile is selected inside a training-selected block without using test outcomes.',
          'Permutation tests shuffle distance labels within heldout game/form/sign groups, never retrain selection; p-values are per arm and stage, not adjusted for multiple arms.',
          'In a 100-vector stage, blocks index the original 100 sampled positions; in other stages they are activation blocks, not token positions.',
          'Token-count mismatches are included and counted; a token-count-only numeric-distance baseline is reported and must be exceeded to pass the candidate gate.',
          'Even equal token counts do not guarantee positional sampling is aligned or that lexical confounds are eliminated.',
          'Synthetic register substitutions are not certified numerical answers.',
          'A passing local distance metric is evidence of geometric numerical structure, not proof of unique shared semantic information or causal use by C0.',
          'A failed metric does not prove the representation has lost numerical information.',
          'No frozen LoRA, Qwen, C0, source run, or capture is modified.'
       ]}
    dest.mkdir(parents=True,exist_ok=False)
    gamer.atomic_json(dest/'final_report.json',result)
    print('GAMER INFORMATION LOCALIZATION COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path)
    p.add_argument('--stages',default=None)
    p.add_argument('--folds',type=int,default=4)
    p.add_argument('--top',type=int,default=8)
    p.add_argument('--tile-width',type=int,default=64)
    p.add_argument('--permutations',type=int,default=99)
    p.add_argument('--seed',type=int,default=47)
    a=p.parse_args()
    run_localization(a.run,output=a.output,
        stages=tuple(s.strip() for s in a.stages.split(',')) if a.stages else None,
        folds=a.folds,top=a.top,tile_width=a.tile_width,
        permutations=a.permutations,seed=a.seed)
