#!/usr/bin/env python3
"""Read-only NanoJev frozen-representation sign-transfer diagnosis.

Compare within-surface and cross-surface linear sign readouts with *identical*
train-group, development-group and test-group partitions. Qwen, LoRA and the
100-vector production head are NEVER trained, written or loaded for inference.
The only fitted quantities are disposable in-memory ridge readouts.

  python -m tools.nanojev_gamer_v1_frozen_transfer_lab --mode oracle
  python -m tools.nanojev_gamer_v1_frozen_transfer_lab --mode analyze \
    --input runtime/reports/gamer-v1/transition-005-sign-information-probe-0001 \
    --output runtime/reports/gamer-v1/transition-005-frozen-transfer-lab-0001

The existing capture supports both directions between source_native and
spine_difference. Hawk–Dove belief examples are test-only, so they are an
out-of-family challenge, NOT independent belief-family recoverability evidence.
A failed linear readout does not prove the information is absent.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.frozen-transfer.v1'
SURFACES=('source_native','spine_difference')
DEFAULT_FEATURES=('final_flat','final_last','final_mean100',
                  'layer_09_mean100','layer_18_mean100','layer_27_mean100',
                  'embedding_mean100')


def _classes(y):
    import numpy as np
    return np.sign(y).astype(np.int8)


def audit(rows):
    """Fail closed on missing/tainted paired labels or any group leakage."""
    if not isinstance(rows,list) or not rows:
        raise ValueError('TRANSFER_EMPTY_DATASET')
    index=defaultdict(list)
    counts=Counter()
    for i,r in enumerate(rows):
        if r['split'] not in ('train','dev','test'):
            raise ValueError('TRANSFER_INVALID_SPLIT')
        if r['family'] not in ('signed_offset','hawk_indifference'):
            raise ValueError('TRANSFER_UNKNOWN_FAMILY')
        index[r['group']].append((i,r))
        counts[(r['split'],r['family'],r['surface'])]+=1
    if len(set(r['id'] for r in rows))!=len(rows):
        raise ValueError('TRANSFER_DUPLICATE_IDS')
    groups=defaultdict(dict)
    for group,items in index.items():
        splits={r['split'] for _,r in items}; families={r['family'] for _,r in items}
        if len(items)!=6 or len(splits)!=1 or len(families)!=1:
            raise ValueError('TRANSFER_LEAKED_OR_INCOMPLETE_GROUP: '+group)
        family=next(iter(families));split=next(iter(splits))
        expected=('spine_belief','game_prose_belief') if family=='hawk_indifference' else SURFACES
        if family=='hawk_indifference' and split!='test':
            raise ValueError('TRANSFER_BELIEF_FAMILY_NOT_TEST_ONLY')
        for _,r in items:
            if r['surface'] not in expected:
                raise ValueError('TRANSFER_UNEXPECTED_SURFACE: '+r['surface'])
            target=Fraction(r['target']); sign=-1 if target<0 else 1 if target>0 else 0
            k=(r['surface'],sign)
            if k in groups[group]:
                raise ValueError('TRANSFER_DUPLICATE_CLASS_OR_SURFACE: '+group)
            groups[group][k]=r
        if set(groups[group])!={(s,c) for s in expected for c in (-1,0,1)}:
            raise ValueError('TRANSFER_MISSING_PAIRED_SIGN_TRIPLES: '+group)
        for sign in (-1,0,1):
            a,b=(groups[group][s,sign] for s in expected)
            if Fraction(a['target'])!=Fraction(b['target']):
                raise ValueError('TRANSFER_MATHEMATICALLY_UNEQUAL_SURFACES: '+group)
    for split in ('train','dev','test'):
        n=counts[(split,'signed_offset',SURFACES[0])]
        if n<6 or counts[(split,'signed_offset',SURFACES[1])]!=n:
            raise ValueError('TRANSFER_UNBALANCED_TRAIN_DEV_TEST_SURFACES: '+split)
        if n%3:
            raise ValueError('TRANSFER_INCOMPLETE_SIGN_TRIPLES')
    return {
        'group_count':len(groups),
        'signed_offset_groups':sum(next(iter({r['family'] for _,r in v}))=='signed_offset' for v in index.values()),
        'cases_by_split_family_surface':[
            {'split':s,'family':f,'surface':sf,'cases':int(count)}
            for (s,f,sf),count in sorted(counts.items())],
        'paired_triples':'PASS', 'holdout_group_overlap':'NONE',
        'capture_coverage':{'signed_offset_bidirectional':'COMPLETE',
                            'hawk_indifference_within_family':'UNAVAILABLE_TEST_ONLY'},
    }


def partition(rows,split,*,surface=None,family='signed_offset'):
    import numpy as np
    return np.asarray([i for i,r in enumerate(rows) if r['split']==split and
                       r['family']==family and (surface is None or r['surface']==surface)],dtype=np.int64)


def paired_stats(rows,indices,classes,values,family='signed_offset'):
    """Correct agreement is separate from merely agreeing on a wrong sign."""
    import numpy as np
    names=SURFACES if family=='signed_offset' else ('spine_belief','game_prose_belief')
    pairs=defaultdict(dict)
    for position,i in enumerate(indices):
        r=rows[int(i)]
        if r['family']!=family:continue
        y=Fraction(r['target']);s=-1 if y<0 else 1 if y>0 else 0
        pairs[r['group']][(r['surface'],s)]=(int(classes[position]),float(values[position]))
    n_pair=0; agree=0; both_correct=0; both_wrong_same=0; groups_complete=0; abs_gaps=[]
    for group,items in pairs.items():
        if set(items)!={(surface,c) for surface in names for c in (-1,0,1)}:
            raise ValueError('TRANSFER_INCOMPLETE_PAIRED_EVALUATION: '+group)
        full=True
        for sign in (-1,0,1):
            ca,va=items[names[0],sign];cb,vb=items[names[1],sign]
            n_pair+=1;agree+=ca==cb;both_correct+=ca==sign and cb==sign
            both_wrong_same+=ca==cb and ca!=sign
            full &= ca==sign and cb==sign
            # Symmetric relative signed-log gap avoids numerical explosion.
            aa=np.sign(va)*np.log1p(abs(va));bb=np.sign(vb)*np.log1p(abs(vb))
            abs_gaps.append(float(abs(aa-bb)))
        groups_complete+=bool(full)
    return {'pairs':n_pair,'groups':len(pairs),
            'class_agreement':float(agree/n_pair) if n_pair else None,
            'both_correct_fraction':float(both_correct/n_pair) if n_pair else None,
            'both_wrong_same_fraction':float(both_wrong_same/n_pair) if n_pair else None,
            'complete_six_case_groups':groups_complete,
            'complete_six_case_fraction':float(groups_complete/len(pairs)) if pairs else None,
            'mean_abs_signed_log_gap':float(np.mean(abs_gaps)) if abs_gaps else None}


def fit_direction(rows,X,y,origin,*,null_shuffles=0,seed=37):
    """Select regularization on origin DEV only; never tune on target surface.

    Source and destination test feature matrices are passed together to one
    kernel fitted on origin TRAIN, so transfer does not use target centering,
    target labels, or target development data. Both directions have 144 train,
    72 source dev, and 72 per-surface heldout signed-offset cases.
    """
    import numpy as np
    itrain=partition(rows,'train',surface=origin) if origin in SURFACES else partition(rows,'train')
    idev=partition(rows,'dev',surface=origin) if origin in SURFACES else partition(rows,'dev')
    itest=partition(rows,'test',family='signed_offset')
    iood=partition(rows,'test',family='hawk_indifference')
    all_test=np.concatenate((itest,iood))
    if not len(itrain) or not len(idev) or not len(itest):
        raise ValueError('TRANSFER_MISSING_ORIGIN_TRAIN_DEV_TEST: '+origin)
    K,Kdev,Ktest=probe._gram_features(X[itrain],X[idev],X[all_test])
    selected,classes,estimate=probe._solve_probe(K,Kdev,Ktest,y[itrain],y[idev])
    metrics={}
    for surface in SURFACES:
        pos=np.asarray([k for k,i in enumerate(all_test) if rows[int(i)]['surface']==surface],dtype=np.int64)
        chosen=all_test[pos]
        metrics[surface]=probe._metrics(y[chosen],estimate[pos],classes[pos],rows,chosen)
    paired=paired_stats(rows,itest,classes[:len(itest)],estimate[:len(itest)])
    # Belief-family examples have no training or development counterparts.
    # Evaluate the readout as a transfer stress test, not within-family decoding.
    other={}
    if len(iood):
        for surface in ('spine_belief','game_prose_belief'):
            pos=np.asarray([k for k,i in enumerate(all_test) if rows[int(i)]['surface']==surface],dtype=np.int64)
            chosen=all_test[pos]
            other[surface]=probe._metrics(y[chosen],estimate[pos],classes[pos],rows,chosen)
    nulls=[]
    for n in range(null_shuffles):
        rng=np.random.default_rng(seed+n)
        shuffled=y[itrain].copy();rng.shuffle(shuffled)
        _,null_cls,null_est=probe._solve_probe(K,Kdev,Ktest,shuffled,y[idev])
        origin_pos=np.asarray([k for k,i in enumerate(all_test) if rows[int(i)]['surface']==(origin if origin in SURFACES else SURFACES[0])],dtype=np.int64)
        ids=all_test[origin_pos]
        nulls.append(probe._metrics(y[ids],null_est[origin_pos],null_cls[origin_pos],rows,ids)['balanced_sign_accuracy'])
    return {'train_surface':origin,'train_count':len(itrain),'development_surface':origin,
            'development_count':len(idev),'selected_on_origin_development_only':True,
            'test_count_by_surface':{s:v['cases'] for s,v in metrics.items()},
            'selection':selected,'test_by_surface':metrics,'test_matched_pairs':paired,
            'test_only_belief_challenge':other,'shuffled_label_null_test_balanced_accuracy':nulls}


def _csvrow(variant,feature,result):
    origin=result['train_surface'];metrics=result['test_by_surface']
    own=[];other=[]
    for surface in SURFACES:
        (own if origin==surface or origin=='both' else other).append(metrics[surface]['balanced_sign_accuracy'])
    return {
        'variant':variant,'feature':feature,'train_surface':origin,
        'alpha':result['selection']['alpha'],
        'train_count':result['train_count'],'dev_count':result['development_count'],
        'dev_origin_sign':result['selection']['dev_balanced_sign_accuracy'],
        'test_native_sign':metrics['source_native']['balanced_sign_accuracy'],
        'test_spine_sign':metrics['spine_difference']['balanced_sign_accuracy'],
        'test_native_triples':metrics['source_native']['triple_accuracy'],
        'test_spine_triples':metrics['spine_difference']['triple_accuracy'],
        'test_origin_sign':own[0] if len(own)==1 else None,
        'test_transfer_sign':other[0] if len(other)==1 else None,
        'test_transfer_gap':(own[0]-other[0]) if len(own)==1 and len(other)==1 else None,
        'test_both_matched_correct':result['test_matched_pairs']['both_correct_fraction'],
        'test_same_class_agreement':result['test_matched_pairs']['class_agreement'],
        'test_both_wrong_same':result['test_matched_pairs']['both_wrong_same_fraction'],
        'test_complete_six_case_fraction':result['test_matched_pairs']['complete_six_case_fraction'],
    }


def _choose_dev_only(results):
    return max(results,key=lambda r:(r['result']['selection']['dev_balanced_sign_accuracy'],
                    -r['result']['selection']['dev_signed_mae'],r['feature']))


def _summary_for(selected):
    """Interpret only pre-selected (by dev) readouts, not best test performers."""
    summary={}
    for variant,conditions in selected.items():
        source=conditions.get('source_native');game=conditions.get('spine_difference')
        if not source or not game:continue
        s=source['result']['test_by_surface'];g=game['result']['test_by_surface']
        s_within=s['source_native']['balanced_sign_accuracy']
        g_within=g['spine_difference']['balanced_sign_accuracy']
        s_to_g=s['spine_difference']['balanced_sign_accuracy']
        g_to_s=g['source_native']['balanced_sign_accuracy']
        summary[variant]={
            'native_selected_feature':source['feature'],
            'spine_selected_feature':game['feature'],
            'same_feature_for_both_directions':source['feature']==game['feature'],
            'native_within':s_within,'spine_within':g_within,
            'native_to_spine':s_to_g,'spine_to_native':g_to_s,
            'native_to_spine_gap':s_within-s_to_g,
            'spine_to_native_gap':g_within-g_to_s,
            'evidence':('BOTH_FORMATS_ACCESSIBLE_TRANSFER_DEFICIT' if min(s_within,g_within)>=0.8 and
                        max(s_within-s_to_g,g_within-g_to_s)>=0.2 else
                        'FORMAT_DECODING_LIMITED_OR_TRANSFER_INCONCLUSIVE'),
        }
    return summary


def verify_output(input_path,output):
    source=Path(input_path).resolve();out=Path(output).resolve()
    archive=source/probe.CAPTURE_NAME if source.is_dir() else source
    if not archive.is_file():raise FileNotFoundError(archive)
    if source==out or source in out.parents or out in source.parents:
        raise ValueError('TRANSFER_OUTPUT_OVERLAPS_INPUT_ARCHIVE')
    if out.exists():raise FileExistsError('TRANSFER_OUTPUT_EXISTS_REFUSE_OVERWRITE: '+str(out))
    return archive,out


def analyze(args):
    import numpy as np
    if args.input is None or args.output is None:
        raise ValueError('TRANSFER_REQUIRES_INPUT_AND_OUTPUT')
    archive,out=verify_output(args.input,args.output)
    meta,arrays=probe._verify_archive(archive,require_source=not args.offline)
    rows=meta['dataset'];audit_result=audit(rows)
    y=np.asarray([float(Fraction(r['target'])) for r in rows],dtype=np.float64)
    if not np.isfinite(y).all():raise ValueError('TRANSFER_NONFINITE_TARGET')
    if args.all_profiles:
        features=sorted(set(k.split('__',1)[1] for k in arrays if k.startswith('lora__')))
    else:
        features=list(DEFAULT_FEATURES if args.features is None else args.features.split(','))
    if not features or any(not feat or feat!=feat.strip() for feat in features) or len(features)!=len(set(features)):
        raise ValueError('TRANSFER_BAD_FEATURE_LIST')
    variants=meta['ablation']
    if not variants or 'lora' not in variants:raise ValueError('TRANSFER_MISSING_LORA_ABLATION')
    for variant in variants:
        for feat in features:
            if variant+'__'+feat not in arrays:
                raise ValueError('TRANSFER_MISSING_CAPTURED_FEATURE: '+variant+'__'+feat)
    policies=[];by_variant=defaultdict(lambda:defaultdict(list))
    for variant in variants:
        for feature in features:
            X=arrays[variant+'__'+feature]
            for origin in (*SURFACES,'both'):
                r=fit_direction(rows,X,y,origin,seed=args.seed)
                item={'variant':variant,'feature':feature,'result':r}
                policies.append(item);by_variant[variant][origin].append(item)
                print('FROZEN_TRANSFER '+variant+'/'+feature+'/'+origin+
                      ' dev='+f'{r["selection"]["dev_balanced_sign_accuracy"]:.4f}'+
                      ' native='+f'{r["test_by_surface"]["source_native"]["balanced_sign_accuracy"]:.4f}'+
                      ' game='+f'{r["test_by_surface"]["spine_difference"]["balanced_sign_accuracy"]:.4f}',flush=True)
    chosen={variant:{origin:_choose_dev_only(v) for origin,v in modes.items()}
            for variant,modes in by_variant.items()}
    # Label-null controls are computed only for pre-selected readouts; doing so
    # for every probed layer would turn a negative control into an expensive sweep.
    controls={}
    for variant,modes in chosen.items():
        controls[variant]={}
        for origin,selection in modes.items():
            res=fit_direction(rows,arrays[variant+'__'+selection['feature']],y,origin,
                              null_shuffles=args.null_shuffles,seed=args.seed)
            controls[variant][origin]=res['shuffled_label_null_test_balanced_accuracy']
    with archive.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    summary=_summary_for(chosen)
    report={
        'schema':SCHEMA,'status':'DIAGNOSTIC_COMPLETE_NO_CAUSAL_CERTIFICATION',
        'capture_sha256':digest,'dataset_sha256':meta['dataset_sha256'],
        'source_model_lineage':meta['lineage'], 'source_fingerprint_verified':not args.offline,
        'dataset_audit':audit_result,'training':'DISPOSABLE_IN_MEMORY_KERNEL_RIDGE_ONLY',
        'head_weights_modified':False,'qwen_lora_modified':False,
        'policy_count':len(policies),
        'feature_policies':features,'variants':variants,
        'development_selected':chosen,'selected_negative_controls':controls,
        'dev_selected_transfer_summary':summary,
        'interpretation':{
            'format_invariance_supported':False,
            'reason':'No automatic causal certification; inspect group-disjoint directional results, sign triples and null controls.',
            'limits':['Linear decodability is sufficient evidence of accessibility only, not proof of general numerical reasoning.',
                      'A negative linear readout does not prove representation information is absent.',
                      'The source archive has no training or development examples of Hawk–Dove belief reasoning.',
                      'This existing held-out split was inspected in earlier v1-v3 experiments; it is NOT virgin acceptance.',
                      'Different feature-location winners cannot establish a causal LoRA effect.',
                      'Ridge alpha and feature selection use development labels only; destination labels do not enter transfer fitting.']},
        'no_promotion_authorized':True,
    }
    if not args.offline:
        report['interpretation']['limits'].append('Capture lineage passed current source checkpoint and adapter verification.')
    else:
        report['interpretation']['limits'].append('Original source checkpoint unavailable/offline: only archived array SHA-256 validated.')
    # No user input may overwrite the archive, any existing experiment, or weights.
    out.mkdir(parents=True,exist_ok=False)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    fields=['variant','feature','train_surface','alpha','train_count','dev_count',
            'dev_origin_sign','test_native_sign','test_spine_sign','test_native_triples','test_spine_triples',
            'test_origin_sign','test_transfer_sign','test_transfer_gap',
            'test_both_matched_correct','test_same_class_agreement','test_both_wrong_same',
            'test_complete_six_case_fraction']
    with (out/'transfer_scores.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for p in policies:writer.writerow(_csvrow(p['variant'],p['feature'],p['result']))
    selected_fields=['variant','origin','feature','dev_origin_sign',
         'test_origin_sign','test_transfer_sign','test_transfer_gap',
         'test_both_matched_correct','test_same_class_agreement','test_complete_six_case_fraction',
         'null_max']
    with (out/'selected_transfer.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=selected_fields);writer.writeheader()
        for variant,modes in chosen.items():
            for origin,sel in modes.items():
                line=_csvrow(variant,sel['feature'],sel['result'])
                writer.writerow({'variant':variant,'origin':origin,'feature':sel['feature'],
                     'dev_origin_sign':line['dev_origin_sign'],
                     'test_origin_sign':line['test_origin_sign'],
                     'test_transfer_sign':line['test_transfer_sign'],
                     'test_transfer_gap':line['test_transfer_gap'],
                     'test_both_matched_correct':line['test_both_matched_correct'],
                     'test_same_class_agreement':line['test_same_class_agreement'],
                     'test_complete_six_case_fraction':line['test_complete_six_case_fraction'],
                     'null_max':max(controls[variant][origin]) if controls[variant][origin] else None})
    print(probe.canonical({'status':report['status'],'output':str(out),
          'policies':len(policies),'selected':{v:{o:{'feature':q['feature'],
          'dev_origin_sign':q['result']['selection']['dev_balanced_sign_accuracy']}
          for o,q in m.items()} for v,m in chosen.items()},
          'directional_summary':summary,'promotion':False}),flush=True)
    return report


def parser():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('oracle','analyze'),default='oracle')
    ap.add_argument('--input',type=Path)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--offline',action='store_true',help='Verify archive but skip local checkpoint/adapter SHA re-verification.')
    ap.add_argument('--all-profiles',action='store_true',help='Analyze every stored earlier-layer profile.')
    ap.add_argument('--features',type=str,help='Comma-separated captured feature names; default includes final flat/last/mean, layers 9/18/27 means and embedding mean.')
    ap.add_argument('--null-shuffles',type=int,default=2)
    ap.add_argument('--seed',type=int,default=37)
    return ap


def main(argv=None):
    args=parser().parse_args(argv)
    if args.null_shuffles<0 or args.null_shuffles>20:
        raise ValueError('TRANSFER_INVALID_NULL_SHUFFLE_COUNT')
    if args.mode=='oracle':
        deck=probe.make_deck()
        print(probe.canonical({'schema':SCHEMA,'deck':audit([x.public() for x in deck]),
                               'dataset_sha256':probe.deck_hash(deck),
                               'status':'ORACLE_ONLY_NO_MODEL_FITTED'}))
    else:
        analyze(args)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
