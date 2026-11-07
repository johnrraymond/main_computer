#!/usr/bin/env python3
"""Frozen GAMER source-sign versus calculated-answer-sign path diagnostic.

Two distinct, explicitly noninterchangeable experiments:
 (1) previously captured signed-register substitutions with KNOWN input sign,
     but UNKNOWN correct game answer after changing a register;
 (2) new independent exact-fraction math-oracle counterfactual triples, in
     which the correct answer's sign changes across negative/zero/positive.

The second experiment reuses the existing sign_information_probe's frozen Qwen
+ LoRA-on/base capture and grouped train/dev/test readout. No production training.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_sign_only as sign
from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_number_distance as distance
from tools import nanojev_gamer_v1_sign_information_probe as oracle
from tools import nanojev_gamer_v1_handoff as gamer

SCHEMA='nanojev.gamer.v1.upstream-to-output-sign-path.v1'
DEFAULT_ARMS=('localized','neighbor_control','random_control')


def _source(sign_run):
    root=Path(sign_run).resolve()
    report=root/'final_report.json'
    if not report.is_file():raise FileNotFoundError('GAMER_UPSTREAM_SIGN_REPORT_MISSING:'+str(report))
    record=json.loads(report.read_text(encoding='utf-8'))
    if record.get('schema')!=sign.SCHEMA or record.get('status')!='COMPLETE_DISPOSABLE_SIGN_ONLY_NOT_PROMOTED':
        raise ValueError('GAMER_UPSTREAM_EXPECTS_SIGN_ONLY_RESULT')
    for path,expected in record.get('source_fingerprints',{}).items():
        if suff._digest(path)!=expected:raise ValueError('GAMER_UPSTREAM_SOURCE_CHANGED:'+path)
    # The input is a completed sign-only result. Its lineage resolver expects
    # the sign experiment's recorded parent (sep/cal/balanced), NOT this result.
    parent_value=record.get('source_run')
    if not parent_value:raise ValueError('GAMER_UPSTREAM_SIGN_PARENT_MISSING')
    parent=Path(parent_value).resolve()
    if parent==root:raise ValueError('GAMER_UPSTREAM_SIGN_PARENT_SELF_REFERENCE')
    if str(parent/'final_report.json') not in record.get('source_fingerprints',{}):
        raise ValueError('GAMER_UPSTREAM_SIGN_PARENT_NOT_FINGERPRINTED')
    balanced,_,capture_report,positions,exp,source,capture,protected=sign._lineage(parent)
    capreport=json.loads((capture/'final_report.json').read_text(encoding='utf-8'))
    if capreport.get('schema')!=distance.SCHEMA or capreport.get('status')!='COMPLETE_READ_ONLY':
        raise ValueError('GAMER_UPSTREAM_BAD_SOURCE_CAPTURE')
    if str(exp)!=str(Path(record['source_expanded']).resolve()):
        raise ValueError('GAMER_UPSTREAM_EXPANDED_SOURCE_MISMATCH')
    for file,sha in record['source_fingerprints'].items():
        if Path(file).name=='activations.npz' and Path(file).parent==capture and sha!=suff._digest(capture/'activations.npz'):
            raise ValueError('GAMER_UPSTREAM_ACTIVATION_DRIFT')
    arms=balanced.get('arms')
    if not arms or arms!=record.get('arms'):
        raise ValueError('GAMER_UPSTREAM_SIGN_CONTROL_ARMS_MISMATCH')
    if sorted(arms)!=sorted((*DEFAULT_ARMS,'full100')):
        raise ValueError('GAMER_UPSTREAM_CONTROL_ARMS_CHANGED')
    return record,arms,exp,source,capture,protected+[report]


def _destination(source,output):
    src=Path(source).resolve()
    if output is not None:
        dest=Path(output).resolve()
        if dest.exists():raise FileExistsError('GAMER_UPSTREAM_OUTPUT_ALREADY_EXISTS:'+str(dest))
    else:
        base=src.with_name(src.name+'-upstream-path')
        dest=next((base.with_name(base.name+f'-{i:04d}') for i in range(1,10000)
                   if not base.with_name(base.name+f'-{i:04d}').exists()),None)
        if dest is None:raise RuntimeError('GAMER_UPSTREAM_NO_NEW_REPORT_SLOT')
    if dest==src or src in dest.parents or dest in src.parents:
        raise ValueError('GAMER_UPSTREAM_OUTPUT_OVERLAPS_SIGN_SOURCE')
    return dest


def _unit(a):
    v=np.asarray(a,dtype=np.float64)
    norm=np.linalg.norm(v)
    return v/norm if norm>1e-12 else None


def _mean_dirs(rows):
    """Equal contribution per independent game/group; raw feature scale removed."""
    chosen=[]
    for x in rows:
        u=_unit(x)
        if u is not None:chosen.append(u)
    if not chosen:return None
    return _unit(np.mean(chosen,axis=0))


def _contrast_score(train_by_group,test_by_group,*,permutations=99,seed=47):
    """Pooled signed contrast ordering. Flip TRAIN groups only for null."""
    tr=[_mean_dirs(group) for group in train_by_group.values()]
    tr=[v for v in tr if v is not None]
    te=[_mean_dirs(group) for group in test_by_group.values()]
    te=[v for v in te if v is not None]
    if len(tr)<2 or len(te)<2:
        return {'status':'INSUFFICIENT_GROUPED_PAIRS','train_groups':len(tr),'test_groups':len(te)}
    axis=_mean_dirs(tr)
    if axis is None:return {'status':'DEGENERATE_SIGN_DIRECTION','train_groups':len(tr),'test_groups':len(te)}
    data=np.stack(te)
    scores=data@axis
    observed=float(np.mean(scores>0)+0.5*np.mean(scores==0))
    rng=np.random.default_rng(seed)
    controls=[]
    train=np.stack(tr)
    for _ in range(permutations):
        n_axis=_mean_dirs(train*rng.choice((-1.,1.),size=(len(train),1)))
        s=data@n_axis if n_axis is not None else np.zeros(len(data))
        controls.append(float(np.mean(s>0)+0.5*np.mean(s==0)))
    return {'status':'COMPLETE','train_groups':len(tr),'test_groups':len(te),
            'group_equal_weight_sign_ordering':observed,
            'test_group_median_signed_cosine':float(np.median(scores)),
            'null_mean':float(np.mean(controls)) if controls else None,
            'null_p_one_sided':float((1+sum(v>=observed for v in controls))/(1+len(controls))) if controls else None,
            'note':'Paired activation contrasts require both variants; signal can be lexical, not arithmetic.'}


def _pair_dirs(activations,values,game_ids,form,train_mags,test_mags,train_games,test_games,*,counts=None,match_token_counts=False):
    """Known sign of a substituted numeral, never inferred game answer sign."""
    v={int(x):i for i,x in enumerate(values)}
    f=distance.FORMS.index(form)
    if match_token_counts and counts is None:
        raise ValueError('GAMER_UPSTREAM_TOKEN_COUNTS_REQUIRED')
    train,test=defaultdict(list),defaultdict(list)
    for gi in train_games:
        for m in train_mags:
            if match_token_counts and counts[gi,1,v[m]]!=counts[gi,1,v[-m]]:continue
            train[str(game_ids[gi])].append(activations[gi,1,v[m]]-activations[gi,1,v[-m]])
    for gi in test_games:
        for m in test_mags:
            if match_token_counts and counts[gi,f,v[m]]!=counts[gi,f,v[-m]]:continue
            test[str(game_ids[gi])].append(activations[gi,f,v[m]]-activations[gi,f,v[-m]])
    return train,test



def _zero_midpoint_geometry(x,values,counts,form,*,magnitudes=(5,6,7,8)):
    """Zero vs +/- same-numerical-magnitude midpoint; NOT correct-zero output."""
    v={int(k):i for i,k in enumerate(values)}
    fi=distance.FORMS.index(form)
    all_ratios=[];matched=[]
    for game in range(x.shape[0]):
        for magnitude in magnitudes:
            lo,mid,hi=v[-magnitude],v[0],v[magnitude]
            neg=np.asarray(x[game,fi,lo],dtype=np.float64)
            zero=np.asarray(x[game,fi,mid],dtype=np.float64)
            pos=np.asarray(x[game,fi,hi],dtype=np.float64)
            width=float(np.linalg.norm(pos-neg))
            if width<=1e-10:continue
            ratio=float(np.linalg.norm(zero-.5*(pos+neg))/width)
            all_ratios.append(ratio)
            if counts[game,fi,lo]==counts[game,fi,mid]==counts[game,fi,hi]:
                matched.append(ratio)
    return {'triples':len(all_ratios),'token_count_matched_triples':len(matched),
        'median_zero_midpoint_offset_over_signed_span':float(np.median(all_ratios)) if all_ratios else None,
        'token_count_matched_median':float(np.median(matched)) if matched else None,
        'interpretation':'Zero midpoint geometry, not a proof that Qwen correctly computes zero-valued answers.'}

def input_sign_grounding(capture,arms,*,permutations=99,seed=47):
    """Train sign-contrast axis on signed numerals 2..4; test 5..8, unseen games/forms."""
    cp=Path(capture)/'final_report.json'
    record=json.loads(cp.read_text(encoding='utf-8'))
    files=[Path(capture)/name for name in ('activations.npz','interventions.jsonl')]
    for file in files:
        if not file.is_file():raise FileNotFoundError(file)
    if len(record['contexts'])<8:raise ValueError('GAMER_UPSTREAM_INSUFFICIENT_INPUT_CONTEXTS')
    v=None
    from tools import nanojev_gamer_v1_radius_activations as radius
    digests={str(file):radius._canonical_hash(file) for file in (cp,*files)}
    with np.load(files[0],allow_pickle=False) as npz:
        values=np.asarray(npz['values'],dtype=int)
        if not set((-8,-7,-6,-5,-4,-3,-2,2,3,4,5,6,7,8)).issubset(set(values)):
            raise ValueError('GAMER_UPSTREAM_CAPTURE_RADIUS_TOO_SMALL')
        c=len(record['contexts']);groups=[r['game_id'] for r in record['contexts']]
        counts=np.asarray(npz['token_counts'])
        if counts.shape!=(c,3,len(values)):
            raise ValueError('GAMER_UPSTREAM_TOKEN_COUNTS_SHAPE')
        folds=distance._group_folds(groups,folds=4,seed=seed)
        out={}
        packed=np.asarray(npz['qwen_final_100'],dtype=np.float32)
        if packed.shape!=(c,3,len(values),102400):
            raise ValueError('GAMER_UPSTREAM_QWEN_CAPTURE_SHAPE_CHANGED')
        raster=packed.reshape(c,3,len(values),100,1024)
        for arm,positions in arms.items():
            # The three four-position arms have comparable input dimensions.
            if arm not in DEFAULT_ARMS:continue
            if len(positions)!=4:raise ValueError('GAMER_UPSTREAM_EXPECTS_FOUR_SAMPLED_POSITIONS')
            x=raster[...,list(positions),:].reshape(c,3,len(values),4096)
            out[arm]={}
            for form in ('signed','digits','words'):
                results=[];matched=[]
                for fi,(tr,te) in enumerate(folds):
                    a,b=_pair_dirs(x,values,groups,form,(2,3,4),(5,6,7,8),tr,te)
                    results.append(_contrast_score(a,b,permutations=permutations,seed=seed+fi*1009))
                    am,bm=_pair_dirs(x,values,groups,form,(2,3,4),(5,6,7,8),tr,te,
                          counts=counts,match_token_counts=True)
                    matched.append(_contrast_score(am,bm,permutations=permutations,seed=seed+fi*1009))
                good=[r for r in results if r['status']=='COMPLETE']
                clean=[r for r in matched if r['status']=='COMPLETE']
                out[arm][form]={
                    'status':'COMPLETE' if len(good)==len(folds) else 'INCOMPLETE_FOLDS',
                    'folds_all_tokens':results,'folds_token_count_matched':matched,
                    'game_equal_weight_accuracy':float(np.mean([r['group_equal_weight_sign_ordering'] for r in good])) if good else None,
                    'token_count_matched_game_accuracy':float(np.mean([r['group_equal_weight_sign_ordering'] for r in clean])) if len(clean)==len(folds) else None,
                    'matched_folds':len(clean),
                    'input_zero_midpoint_geometry':_zero_midpoint_geometry(x,values,counts,form),
                    'notes':'All-token results can reflect numeral length. Matched results exclude +/- token-count mismatch; source input sign is NOT a certified game answer.'}
            del x
        del packed,raster
    for path,sha in digests.items():
        if radius._canonical_hash(path)!=sha:raise ValueError('GAMER_UPSTREAM_CAPTURE_CHANGED_DURING_RUN')
    return {'status':'COMPLETE_READ_ONLY','arms':out,
        'train_magnitudes':[2,3,4],'test_magnitudes':[5,6,7,8],
        'train_form':'signed','test_forms':['signed','digits','words'],
        'game_folds':4,'capture_games':len(groups),
        'limitations':['Input values are synthetically substituted without recomputing certified game answers.',
         'These are two-activation sign contrasts, not stand-alone numerical predictions.',
         'Lexical plus/minus marks or tokenization can explain sign contrast.',
         'Localized positions were selected on this original synthetic capture, so these are exploratory results.'],
        'source_sha256':digests}


def _triples(meta,arrays,variant,positions,*,split,feature='final_flat'):
    """Exact oracle labels only; train/test grouping by entire mathematical triple."""
    source=arrays[variant+'__'+feature]
    idx={}
    for i,row in enumerate(meta['dataset']):
        if row['split']!=split:continue
        key=(row['group'],row['surface']);idx.setdefault(key,{})
        truth=int(np.sign(float(Fraction(row['target']))))
        if truth in idx[key]:raise ValueError('GAMER_UPSTREAM_DUPLICATE_ORACLE_TRIPLE_CLASS')
        idx[key][truth]=i
    pairs=defaultdict(list)
    for (game,surface),indices in idx.items():
        if set(indices)!={-1,0,1}:raise ValueError('GAMER_UPSTREAM_INCOMPLETE_ORACLE_TRIPLE')
        a=source[indices[1]];b=source[indices[-1]]
        if positions is not None:
            a=a.reshape(100,1024)[list(positions)].reshape(-1)
            b=b.reshape(100,1024)[list(positions)].reshape(-1)
        pairs[(game,meta['dataset'][indices[0]]['family'])].append(np.asarray(a,dtype=np.float32)-np.asarray(b,dtype=np.float32))
    return pairs


def _head_triple_metrics(meta,arrays):
    """Original frozen C0, not a classifier fitted to the oracle."""
    rows=meta['dataset'];pred=arrays['production_head_predictions']
    grouped=defaultdict(dict)
    for i,row in enumerate(rows):
        if row['split']!='test':continue
        target=float(Fraction(row['target']));key=(row['group'],row['surface'],row['family'])
        klass=int(np.sign(target))
        if klass in grouped[key]:raise ValueError('GAMER_UPSTREAM_DUPLICATE_HEAD_TRIPLE')
        grouped[key][klass]=(target,float(pred[i]))
    byfam=defaultdict(list)
    for (group,surface,family),triple in grouped.items():
        if set(triple)!={-1,0,1}:raise ValueError('GAMER_UPSTREAM_MISSING_HEAD_ORACLE_TRIPLE')
        lo=triple[-1][1];zz=triple[0][1];hi=triple[1][1]
        byfam[family].append((lo<0.,abs(zz)<=.05,hi>0.,hi>lo))
    out={}
    for family,rows in byfam.items():
        x=np.asarray(rows,dtype=bool)
        out[family]={'triples':len(x),'negative_prediction_sign_correct':float(np.mean(x[:,0])),
             'zero_within_0_05':float(np.mean(x[:,1])),
             'positive_prediction_sign_correct':float(np.mean(x[:,2])),
             'positive_output_greater_than_negative':float(np.mean(x[:,3])),
             'whole_triple_correct':float(np.mean(np.all(x[:,:3],axis=1)))}
    return out


def math_oracle_path(metadata,arrays,arms,*,permutations=99,seed=47):
    """Computed-answer-sign contrasts in Qwen, with independently computed Fraction targets."""
    if metadata.get('schema')!=oracle.SCHEMA:raise ValueError('GAMER_UPSTREAM_ORACLE_SCHEMA_MISMATCH')
    if metadata.get('dataset_sha256')!=oracle.deck_hash(oracle.make_deck(
          metadata['deck']['by_split']['train']//6,
          metadata['deck']['by_split']['dev']//6,
          (metadata['deck']['by_split']['test']//6)-(6 if 'hawk_indifference' in metadata['deck']['by_family'] else 0),
          'hawk_indifference' in metadata['deck']['by_family'])):
        raise ValueError('GAMER_UPSTREAM_ORACLE_DATASET_CHANGED')
    output={}
    for variant in metadata['ablation']:
        output[variant]={}
        for arm,positions in arms.items():
            if arm not in DEFAULT_ARMS:continue
            tr=_triples(metadata,arrays,variant,positions,split='train')
            te=_triples(metadata,arrays,variant,positions,split='test')
            record={}
            for family in sorted({k[1] for k in te}):
                selected={k:v for k,v in te.items() if k[1]==family}
                record[family]=_contrast_score(tr,selected,permutations=permutations,seed=seed)
            output[variant][arm]=record
    return {'status':'COMPLETE_EXACT_ORACLE_TRIPLES',
       'computed_answer_sign_contrasts':output,
       'frozen_production_c0_test_triples':_head_triple_metrics(metadata,arrays),
       'oracle_dataset_sha256':metadata['dataset_sha256'],
       'limitations':['Target signs are independently recomputed exact Fraction answers for controlled mathematical prompts.',
          'The mathematical deck does not contain all original GAMER certified games.',
          'A direction or trained ridge head may exploit prompt templates or lexical comparison cues rather than general arithmetic.',
          'Qwen contrast access does not show that production C0 reads or correctly uses the sign.',
          'Comparison of these oracle triples with synthetic input-numeral substitutions is qualitative, not a causal bottleneck localization.']}


def run_upstream_path(run,*,output=None,device='cuda',capture_batch=2,
      train_groups=24,dev_groups=12,test_groups=12,permutations=99,
      input_only=False,skip_base=False):
    if min(train_groups,dev_groups,test_groups)<2 or capture_batch<1 or permutations<0 or permutations>999:
        raise ValueError('GAMER_UPSTREAM_INVALID_EXPERIMENT_BUDGET')
    record,arms,exp,source,capture,protected=_source(run)
    dest=_destination(run,output)
    for folder in (exp,source,capture):
        folder=Path(folder).resolve()
        if dest==folder or folder in dest.parents or dest in folder.parents:
            raise ValueError('GAMER_UPSTREAM_OUTPUT_OVERLAPS_PROTECTED_SOURCE')
    model_id=json.loads((exp/'provenance.json').read_text(encoding='utf-8'))['model']
    print('GAMER UPSTREAM-TO-OUTPUT SIGN PATH: '+str(dest),flush=True)
    print('  frozen Qwen / LoRA / production C0; known input sign vs exact mathematical answer sign; NO TRAINING',flush=True)
    dest.mkdir(parents=True,exist_ok=False)
    input_report=input_sign_grounding(capture,arms,permutations=permutations)
    gamer.atomic_json(dest/'input_sign_grounding.json',input_report)
    for arm,forms in input_report['arms'].items():
        print('  signed numeral grounding '+arm+': signed='+str(forms['signed']['game_equal_weight_accuracy'])+
              ' words='+str(forms['words']['game_equal_weight_accuracy']),flush=True)
    report={'schema':SCHEMA,'status':'INPUT_SIGN_ONLY_ORACLE_NOT_TESTED' if input_only else 'IN_PROGRESS',
       'sign_only_source':str(Path(run).resolve()),'source_expanded':str(exp),
       'source_capture':str(capture),'source_sign_only_sha256':suff._digest(Path(run)/'final_report.json'),
       'input_sign_grounding':input_report,
       'prior_certified_answer_sign':{'localized_all_auc':record['results']['localized']['all']['auc'],
          'localized_reserved_auc':record['results']['localized']['reserved_untrained_magnitudes']['auc'],
          'certified_nonzero_cases':record['coverage']['dev_nonzero_cases']},
       'no_weight_update':True,'promotion':'BLOCKED_DIAGNOSTIC_ONLY'}
    if not input_only:
        namespace=argparse.Namespace(run=source,expanded_run=exp,output=dest/'oracle-capture',
           model_id=model_id,device=device,capture_batch=capture_batch,head_batch=4,
           max_input_tokens=min(1024,int(json.loads((exp/'provenance.json').read_text())['configuration']['max_length'])),
           train_groups=train_groups,dev_groups=dev_groups,test_groups=test_groups,
           skip_ood=False,skip_base_ablation=skip_base,skip_full_vector=False,
           allow_model_download=False,offline=False,seed=47)
        print('  certified exact-oracle counterfactuals: train='+str(train_groups)+
            ' dev='+str(dev_groups)+' test='+str(test_groups)+' groups plus unseen family',flush=True)
        oracle.capture(namespace)
        analyzed=oracle.analyze(argparse.Namespace(input=namespace.output,offline=False,
               skip_full_vector=False,seed=47))
        meta,arrays=oracle._verify_archive(namespace.output/oracle.CAPTURE_NAME,require_source=True)
        report['math_oracle']=math_oracle_path(meta,arrays,arms,permutations=permutations)
        report['oracle_capture_dir']=str(namespace.output)
        report['oracle_analysis_sha256']=suff._digest(namespace.output/'analysis.json')
        report['oracle_conclusion']=analyzed['conclusion']
        report['oracle_development_selected_readouts']={
            key:{'feature':v['feature'],'dev_balanced_sign':v['selection']['dev_balanced_sign_accuracy'],
                 'test_balanced_sign':v['test']['balanced_sign_accuracy'],
                 'test_triple_accuracy':v['test']['triple_accuracy']}
                 for key,v in analyzed['development_selected_readouts'].items()}
        for fam,row in report['math_oracle']['frozen_production_c0_test_triples'].items():
            print('  exact oracle C0 '+fam+': sign_reversal_order='+str(row['positive_output_greater_than_negative'])+
                ' whole_triple='+str(row['whole_triple_correct']),flush=True)
        report['status']='COMPLETE_READ_ONLY_WITH_EXACT_ORACLE'
    # Fail closed on fingerprint drift in all upstream datasets/checkpoints.
    for path,sha in record.get('source_fingerprints',{}).items():
        if suff._digest(path)!=sha:raise ValueError('GAMER_UPSTREAM_SOURCE_CHANGED_AFTER_ANALYSIS:'+path)
    if suff._digest(Path(run)/'final_report.json')!=report['source_sign_only_sha256']:
        raise ValueError('GAMER_UPSTREAM_SIGN_REPORT_CHANGED')
    gamer.atomic_json(dest/'final_report.json',report)
    print('GAMER UPSTREAM SIGN PATH COMPLETE: '+str(dest/'final_report.json'),flush=True)
    return report
