#!/usr/bin/env python3
"""GAMER familiar-versus-unseen game transfer audit for saved disposable heads.

Read-only replay of the exact previously fitted frozen-Qwen sufficiency heads.
On each dev case, compare one head that fitted on OTHER prompts from the same
GAME with the head that excluded that entire game. Both score the same unseen
prompt, with a per-answer-class confusion audit and separate reserved-magnitude
comparison. No model training or original checkpoint modification occurs.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_sufficiency as suff
from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_number_distance as dist

SCHEMA='nanojev.gamer.v1.readout-generalization.v1'
CLASS_NAMES=('negative','zero','positive')
ARM_NAMES=('localized','neighbor_control','random_control','full100')


def _read_training_rows(source):
    manifest=json.loads((source/'handoff_manifest.json').read_text(encoding='utf-8'))
    cases,_=gamer.load_cases(source/'certified_pairs.jsonl',
        allow_partial=bool(manifest.get('allow_partial_smoke')),
        include_catalog_extensions=bool(manifest.get('bounded_catalog_extensions')),
        include_transition_games=bool(manifest.get('bounded_transition_games')))
    return gamer.by_split(cases,'train')


def _audit_predictions(y,pred,classes):
    """Detailed confusion audit: truth is defined by certified targets, not logits."""
    y=np.asarray(y,dtype=np.float64); pred=np.asarray(pred,dtype=np.float64)
    classes=np.asarray(classes,dtype=np.int64)
    if y.ndim!=1 or pred.shape!=y.shape or classes.shape!=y.shape or len(y)==0:
        raise ValueError('GAMER_GENERALIZATION_BAD_PREDICTIONS')
    if not np.isfinite(y).all() or not np.isfinite(pred).all() or not np.isin(classes,[0,1,2]).all():
        raise ValueError('GAMER_GENERALIZATION_NONFINITE_PREDICTIONS')
    true=suff._classes(y); cm=np.zeros((3,3),dtype=int)
    for a,b in zip(true,classes):cm[a,b]+=1
    detail={}
    for i,name in enumerate(CLASS_NAMES):
        chosen=true==i;num=int(chosen.sum())
        detail[name]={
            'cases':num,
            'predicted_as':{CLASS_NAMES[j]:int(cm[i,j]) for j in range(3)},
            'class_recall':float(cm[i,i]/num) if num else None,
            'mae':float(np.abs(y[chosen]-pred[chosen]).mean()) if num else None,
            'median_absolute_error':float(np.median(np.abs(y[chosen]-pred[chosen]))) if num else None,
            'predicted_zero_fraction':float(cm[i,1]/num) if num else None,
        }
    nonzero=true!=1; zeros=true==1
    smallneg=(y<0)&(y>=-1);smallpos=(y>0)&(y<=1)
    supported=[detail[name]['class_recall'] for name in CLASS_NAMES if detail[name]['cases']]
    return {
      'cases':len(y),'mae':float(np.abs(y-pred).mean()),
      'exact_numeric_rate':float((np.abs(y-pred)<=1e-6).mean()),
      'three_class_accuracy':float((true==classes).mean()),
      'macro_class_recall':float(np.mean(supported)),
      'always_positive_three_class_accuracy':float((true==2).mean()),
      'always_positive_nonzero_sign_accuracy':float(np.mean(y[nonzero]>0)) if nonzero.any() else None,
      'nonzero_sign_accuracy':float((np.sign(y[nonzero])==np.sign(pred[nonzero])).mean()) if nonzero.any() else None,
      'small_negative_cases':int(smallneg.sum()),
      'small_negative_sign_accuracy':float((np.sign(y[smallneg])==np.sign(pred[smallneg])).mean()) if smallneg.any() else None,
      'small_positive_cases':int(smallpos.sum()),
      'small_positive_sign_accuracy':float((np.sign(y[smallpos])==np.sign(pred[smallpos])).mean()) if smallpos.any() else None,
      'zero_exact_rate':float((np.abs(pred[zeros])<=1e-6).mean()) if zeros.any() else None,
      'zero_false_positive_rate':float((classes[nonzero]==1).mean()) if nonzero.any() else None,
      'zero_precision':float(cm[1,1]/cm[:,1].sum()) if cm[:,1].sum() else None,
      'true_class_confusion':detail,
    }


def _paired_summary(rows,seed=47,permutations=999):
    """Same dev rows, different game-disjoint vs game-familiar fold models.

    Game-cluster sign flips are exploratory: model weights/training histories
    differ between folds, so a gap cannot be given a causal interpretation.
    """
    if not rows: return {'status':'BLOCKED_NO_PAIRED_ROWS'}
    y=np.asarray([r['target'] for r in rows],dtype=float)
    pf=np.asarray([r['familiar_prediction'] for r in rows],dtype=float)
    pu=np.asarray([r['unseen_prediction'] for r in rows],dtype=float)
    cf=np.asarray([r['familiar_class'] for r in rows],dtype=int)
    cu=np.asarray([r['unseen_class'] for r in rows],dtype=int)
    f=_audit_predictions(y,pf,cf);u=_audit_predictions(y,pu,cu)
    tc=suff._classes(y)
    abs_delta=np.abs(y-pu)-np.abs(y-pf)
    class_delta=(cf==tc).astype(float)-(cu==tc).astype(float)
    bygame=defaultdict(list)
    for r,delta in zip(rows,abs_delta):bygame[r['game_id']].append(float(delta))
    game_deltas=np.asarray([np.mean(v) for k,v in sorted(bygame.items())])
    rng=np.random.default_rng(seed)
    obs=float(game_deltas.mean())
    sampled=rng.choice([-1.,1.],size=(permutations,len(game_deltas)))
    trial=(sampled*game_deltas).mean(axis=1)
    p=float((1+int((np.abs(trial)>=abs(obs)-1e-12).sum()))/(1+permutations))
    discordant={
      'familiar_correct_unseen_wrong':int(np.sum((cf==tc)&(cu!=tc))),
      'unseen_correct_familiar_wrong':int(np.sum((cu==tc)&(cf!=tc)))
    }
    return {
       'status':'COMPLETE_PAIRED_SAME_CERTIFIED_DEV_ROWS',
       'cases':len(rows),'games':len(bygame),
       'familiar_game':f,'unseen_game':u,
       'mae_advantage_of_familiar':float(abs_delta.mean()),
       'macro_class_recall_advantage_of_familiar':f['macro_class_recall']-u['macro_class_recall'],
       'three_class_accuracy_advantage_of_familiar':float(class_delta.mean()),
       'game_equal_weight_mae_advantage_of_familiar':obs,
       'game_cluster_signflip_two_sided_p_exploratory':p,
       'paired_class_decision_discordance':discordant,
       'by_true_class':{
           name:{'cases':int((tc==i).sum()),
                 'familiar_recall':f['true_class_confusion'][name]['class_recall'],
                 'unseen_recall':u['true_class_confusion'][name]['class_recall'],
                 'familiar_mae':f['true_class_confusion'][name]['mae'],
                 'unseen_mae':u['true_class_confusion'][name]['mae']}
           for i,name in enumerate(CLASS_NAMES)},
    }



def _assemble_arm_pairs(arm,eligibility,groups,computed,dev,labels):
    """Pair only cases with an independently fitted game-familiar head.

    The fold holding out a game is unique, but every OTHER fold can happen to
    put that game in validation. Validation influenced checkpoint selection;
    treating one as familiar would leak evaluation information into the
    comparison. Such cases remain in unseen_all, but not in paired comparisons.
    """
    paired=[];unmatched=[];unseen_all=[]
    for i in eligibility:
        i=int(i);game=str(dev[i]['game_id'])
        options=[(int(f['fold']),computed[(arm,int(f['fold']),i)]) for f in groups
                 if (arm,int(f['fold']),i) in computed]
        hold=[(fi,d) for fi,d in options if d['cohort']=='unseen_game']
        familiar=[(fi,d) for fi,d in options if d['cohort']=='familiar_game']
        if len(hold)!=1:
            raise ValueError('GAMER_GENERALIZATION_UNSEEN_FOLD_NOT_UNIQUE:'+str(i))
        uidx,ur=hold[0]
        target=float(labels[i]);label=CLASS_NAMES[int(suff._classes([target])[0])]
        unseen_all.append({'game_id':game,'target':target,
                           'unseen_prediction':ur['prediction'],'unseen_class':ur['class'],
                           'dev_case_index':i})
        if not familiar:
            validation_folds=[int(f['fold']) for f in groups if game in f['val_games']]
            nonhold_folds={int(f['fold']) for f in groups if int(f['fold'])!=uidx}
            if set(validation_folds)!=nonhold_folds:
                raise ValueError('GAMER_GENERALIZATION_UNEXPLAINED_MISSING_FAMILIAR:'+str(i))
            unmatched.append({'dev_case_index':i,'game_id':game,'target':target,
                'true_class':label,'unseen_fold':uidx,'validation_folds':validation_folds,
                'reason':'VALIDATION_IN_ALL_OTHER_FOLDS'})
            continue
        # Choose by fold index, never by observed predictions or labels.
        fidx,fr=min(familiar,key=lambda t:t[0])
        paired.append({'arm':arm,'dev_case_index':i,'game_id':game,
            'target':target,'true_class':label,'familiar_fold':fidx,'unseen_fold':uidx,
            'familiar_prediction':fr['prediction'],'unseen_prediction':ur['prediction'],
            'familiar_class':fr['class'],'unseen_class':ur['class'],
            'familiar_zero_probability':fr['zero_probability'],
            'unseen_zero_probability':ur['zero_probability'],
            'reserved_magnitude':suff._reserved_numeric_range(target)})
    if len(paired)+len(unmatched)!=len(eligibility) or len(unseen_all)!=len(eligibility):
        raise ValueError('GAMER_GENERALIZATION_CASE_ACCOUNTING_FAILED')
    return paired,unmatched,unseen_all


def _coverage(paired,unmatched,unseen_all):
    if len(paired)+len(unmatched)!=len(unseen_all):
        raise ValueError('GAMER_GENERALIZATION_COVERAGE_MISMATCH')
    return {'eligible_cases':len(unseen_all),'paired_cases':len(paired),
            'unmatched_cases':len(unmatched),
            'paired_fraction':len(paired)/len(unseen_all) if unseen_all else 0.,
            'unmatched_by_true_class':dict(Counter(r['true_class'] for r in unmatched)),
            'unmatched_games':sorted({r['game_id'] for r in unmatched}),
            'unmatched_case_details':unmatched,
            'unmatched_cases_still_in_full_unseen_audit':True,
            'reason':'Validation-only in every other fold; not a valid familiar head.'}


def _unseen_all_metrics(unseen_all):
    if not unseen_all:return {'status':'BLOCKED_NO_HELDOUT_CASES'}
    return _audit_predictions(
        [r['target'] for r in unseen_all],
        [r['unseen_prediction'] for r in unseen_all],
        [r['unseen_class'] for r in unseen_all])


def _interpret_gap(paired):
    """Conservative diagnostic classification, not a production acceptance gate.

    Never diagnose a game-transfer failure until a head can first resolve the
    three classes on entirely untrained DEV prompts from games it fitted on.
    """
    if paired.get('status')!='COMPLETE_PAIRED_SAME_CERTIFIED_DEV_ROWS':
        return {'status':'BLOCKED_NO_PAIRED_EVIDENCE'}
    fam=paired['familiar_game'];nov=paired['unseen_game']
    counts=fam['true_class_confusion']
    thresholds={'minimum_cases_per_sign_class':8,'minimum_familiar_class_recall':.8,
                'minimum_familiar_zero_precision':.8,'maximum_familiar_false_zero_rate':.1,
                'material_class_transfer_gap':.1}
    if any(counts[c]['cases']<8 for c in CLASS_NAMES):
        status='INSUFFICIENT_CLASS_COVERAGE'
    elif (any(counts[c]['class_recall']<.8 for c in CLASS_NAMES) or
          fam['zero_precision'] is None or fam['zero_precision']<.8 or
          fam['zero_false_positive_rate'] is None or fam['zero_false_positive_rate']>.1):
        status='FAMILIAR_GAME_SIGN_ZERO_READOUT_NOT_LEARNED'
    elif fam['macro_class_recall']-nov['macro_class_recall']>.1:
        status='GAME_DISJOINT_TRANSFER_DEFICIT_AFTER_FAMILIAR_COMPETENCE'
    else:
        status='NO_MATERIAL_CLASS_TRANSFER_GAP_AT_SCREENING_THRESHOLDS'
    return {'status':status,'thresholds':thresholds,
            'familiar_macro_recall':fam['macro_class_recall'],
            'unseen_macro_recall':nov['macro_class_recall'],
            'not_a_production_or_zero_certification_gate':True}


def _verify_source_report(suff_run):
    root=Path(suff_run).resolve(); path=root/'final_report.json'
    if not path.is_file():raise FileNotFoundError('GAMER_GENERALIZATION_MISSING_SUFFICIENCY_REPORT:'+str(path))
    report=json.loads(path.read_text(encoding='utf-8'))
    if report.get('schema')!=suff.SCHEMA or report.get('status')!='COMPLETE_EXPERIMENTAL_NOT_PROMOTED':
        raise ValueError('GAMER_GENERALIZATION_BAD_SUFFICIENCY_LINEAGE')
    if report.get('promotion','').startswith('PASS'):
        raise ValueError('GAMER_GENERALIZATION_UNEXPECTED_PROMOTION')
    for path_str,expected in report.get('source_fingerprints',{}).items():
        if suff._digest(Path(path_str))!=expected:
            raise ValueError('GAMER_GENERALIZATION_SOURCE_FINGERPRINT_CHANGED:'+path_str)
    required={'source_causal','source_expanded','selected_positions','arms','settings','holdout','results'}
    if not required.issubset(report):raise ValueError('GAMER_GENERALIZATION_INCOMPLETE_SOURCE_REPORT')
    return root,report


def _check_checkpoint(torch,path,fold,arm,positions,source_hash,input_dim,hidden,device):
    if not path.is_file():raise FileNotFoundError('GAMER_GENERALIZATION_MISSING_HEAD:'+str(path))
    # weights_only=True prevents executable Python objects in serialized checkpoints.
    data=torch.load(path,map_location='cpu',weights_only=True)
    if data.get('schema')!=suff.SCHEMA:raise ValueError('GAMER_GENERALIZATION_HEAD_SCHEMA_MISMATCH')
    meta=data.get('metadata',{})
    if meta.get('fold')!=fold or meta.get('mode')!=arm or meta.get('positions')!=positions or meta.get('source_checkpoint_sha256')!=source_hash:
        raise ValueError('GAMER_GENERALIZATION_CHECKPOINT_LINEAGE_MISMATCH')
    net=suff._model(torch,input_dim,hidden).to(device)
    net.load_state_dict(data['state_dict'],strict=True);net.eval()
    return net


def _verify_prior_holdout(path,dev,arm,fold,ids,pred,classes,prob):
    # Caller supplies exact previously reported rows, not a metric-only check.
    # A changed prediction implementation must not quietly masquerade as the
    # prior experiment's output.
    for j,i in enumerate(ids):
        key=(arm,int(fold),int(i))
        try:expected=path[key]
        except KeyError:raise ValueError('GAMER_GENERALIZATION_MISSING_PRIOR_HELDOUT_PREDICTION:'+str(key))
        if (expected['game_id']!=dev[int(i)]['game_id'] or
            abs(float(expected['target'])-float(dev[int(i)]['answer']))>1e-7 or
            int(expected['predicted_class'])!=int(classes[j]) or
            abs(float(expected['prediction'])-float(pred[j]))>0.005 or
            abs(float(expected['zero_probability'])-float(prob[j,1]))>0.005):
            raise ValueError('GAMER_GENERALIZATION_REPLAY_PARITY_FAILED:'+str(key))


def _read_prior_predictions(path):
    if not path.is_file():raise FileNotFoundError('GAMER_GENERALIZATION_MISSING_PRIOR_PREDICTIONS:'+str(path))
    seen={}
    with path.open(newline='',encoding='utf-8') as f:
        for row in csv.DictReader(f):
            key=(row['mode'],int(row['fold']),int(row['dev_case_index']))
            if key in seen:raise ValueError('GAMER_GENERALIZATION_DUPLICATE_PRIOR_PREDICTION')
            seen[key]=row
    return seen


def run_generalization(suff_run,*,output=None,device='cuda',batch_size=64,
                       arms=None,seed=47,permutations=999):
    if batch_size<1 or batch_size>1024 or not 99<=permutations<=9999:
        raise ValueError('GAMER_GENERALIZATION_INVALID_BUDGET')
    selected=tuple(arms) if arms is not None else ARM_NAMES
    if not selected or len(selected)!=len(set(selected)) or any(a not in ARM_NAMES for a in selected):
        raise ValueError('GAMER_GENERALIZATION_INVALID_ARMS')
    import torch
    if device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('GAMER_GENERALIZATION_CUDA_UNAVAILABLE')
    root,rep=_verify_source_report(suff_run)
    causal,causal_report,capture_report,_,positions,exp,source,capture=suff._source(rep['source_causal'])
    if str(exp)!=str(Path(rep['source_expanded']).resolve()) or list(positions)!=list(rep['selected_positions']):
        raise ValueError('GAMER_GENERALIZATION_WRONG_ORIGINAL_FEATURE_LINEAGE')
    arms_map=suff._arms(positions,seed=int(rep['settings']['seed']))
    if arms_map!=rep['arms']:raise ValueError('GAMER_GENERALIZATION_ARM_SELECTION_CHANGED')
    out=dist._destination(root,output,'-generalization')
    for parent in (root,causal,exp,source,capture,Path(causal_report['source_localization']).resolve()):
        if out==parent or out in parent.parents or parent in out.parents:
            raise ValueError('GAMER_GENERALIZATION_OUTPUT_OVERLAPS_SOURCE')
    identity=json.loads((exp/'provenance.json').read_text(encoding='utf-8'))
    train=_read_training_rows(source)
    dev,xdev,pdev=suff._cached_split(exp,source,identity,'dev')
    train_prompts={hashlib.sha256(gamer.original_prompt(r).encode('utf-8')).hexdigest() for r in train}
    calib=[str(c['game_id']) for c in capture_report['contexts']]
    settings=rep['settings']
    groups=suff._split_groups(train,dev,calib,folds=int(settings['folds']),seed=int(settings['seed']))
    if len(rep['holdout']['folds'])!=len(groups):raise ValueError('GAMER_GENERALIZATION_FOLD_COUNT_CHANGED')
    for f,old in zip(groups,rep['holdout']['folds']):
        if f['fold']!=old['fold'] or f['test_games']!=old['heldout_games'] or len(f['test'])!=old['test_cases']:
            raise ValueError('GAMER_GENERALIZATION_FOLD_SPLIT_CHANGED')
    eligibility=sorted({int(i) for fold in groups for i in fold['test']})
    if any(hashlib.sha256(gamer.original_prompt(dev[i]).encode('utf-8')).hexdigest() in train_prompts for i in eligibility):
        raise ValueError('GAMER_GENERALIZATION_DUPLICATE_TRAIN_DEV_PROMPT')
    if len(eligibility)!=int(rep['certified_split_sizes']['scored_dev']):
        raise ValueError('GAMER_GENERALIZATION_DEV_COHORT_CHANGED')
    previous_path=root/'heldout_predictions.csv'
    recorded=_read_prior_predictions(previous_path)
    paths=[root/'final_report.json',previous_path,pdev,exp/'provenance.json',exp/'best_model.pt']
    checkpoints={}
    for arm in selected:
        for fold in groups:
            file=root/'experimental_models'/f'fold{fold["fold"]+1:02d}_{arm}.pt'
            paths.append(file);checkpoints[(arm,fold['fold'])]=file
    initial={str(p):suff._digest(p) for p in paths}
    out_rows=[];computed={};per_fold={}
    print('GAMER READOUT GENERALIZATION: '+str(out),flush=True)
    print('  existing disposable checkpoints ONLY; frozen Qwen/LoRA/C0; NO TRAINING',flush=True)
    print(f'  certified dev cases={len(eligibility)}; {len(groups)} original heldout-game folds',flush=True)
    labels=np.asarray([float(r['answer']) for r in dev],dtype=float)
    for arm in selected:
        for fold in groups:
            fi=fold['fold'];fit=set(fold['fit_games']);val=set(fold['val_games']);test=set(fold['test_games'])
            shape=1024*(100 if arms_map[arm] is None else len(arms_map[arm]))
            hidden=min(int(settings['hidden']),16) if arms_map[arm] is None else int(settings['hidden'])
            net=_check_checkpoint(torch,checkpoints[(arm,fi)],fi,arm,arms_map[arm],
                causal_report['checkpoint_sha256'],shape,hidden,device)
            # Every dev row is out-of-sample as a prompt. Classify by whether
            # this PARTICULAR model fitted other prompts for its game.
            ids=np.asarray([i for i in eligibility if dev[i]['game_id'] in fit|val|test],dtype=int)
            pred,cls,probs=suff._predict(torch,net,xdev,ids,arms_map[arm],device=device,batch_size=batch_size)
            byid={int(i):(float(p),int(c),float(q[1]),float(q[0]),float(q[2]))
                  for i,p,c,q in zip(ids,pred,cls,probs)}
            _verify_prior_holdout(recorded,dev,arm,fi,fold['test'],
                 np.asarray([byid[int(i)][0] for i in fold['test']]),
                 np.asarray([byid[int(i)][1] for i in fold['test']]),
                 np.asarray([[byid[int(i)][3],byid[int(i)][2],byid[int(i)][4]] for i in fold['test']]))
            for i in ids:
                i=int(i);game=dev[i]['game_id'];cohort='familiar_game' if game in fit else ('validation_game' if game in val else 'unseen_game')
                if cohort=='validation_game':continue  # used during early stopping, unblinded
                p,c,pz,pn,pp=byid[i]
                computed[(arm,fi,i)]={'prediction':p,'class':c,'zero_probability':pz,'cohort':cohort}
                out_rows.append({'arm':arm,'fold':fi,'dev_case_index':i,'game_id':game,
                    'prompt_sha256':hashlib.sha256(gamer.original_prompt(dev[i]).encode('utf-8')).hexdigest(),
                    'cohort':cohort,'target':labels[i],'true_class':CLASS_NAMES[int(suff._classes([labels[i]])[0])],
                    'prediction':p,'predicted_class':CLASS_NAMES[c],
                    'zero_probability':pz,'reserved_magnitude':suff._reserved_numeric_range(labels[i])})
            fit_ids=np.asarray([i for i in ids if dev[int(i)]['game_id'] in fit],dtype=int)
            unseen_ids=np.asarray(fold['test'],dtype=int)
            if not len(fit_ids):raise ValueError('GAMER_GENERALIZATION_NO_GAME_FAMILIAR_DEV_ROWS')
            def cohort_metric(sub):
                return _audit_predictions(labels[sub],
                    [byid[int(i)][0] for i in sub],[byid[int(i)][1] for i in sub])
            per_fold[(arm,fi)]={'fit_game_dev':cohort_metric(fit_ids),
                                 'unseen_game_dev':cohort_metric(unseen_ids),
                                 'familiar_dev_cases':len(fit_ids),'heldout_dev_cases':len(unseen_ids)}
            print(f'  fold {fi+1}/{len(groups)} {arm}: familiar class={per_fold[(arm,fi)]["fit_game_dev"]["three_class_accuracy"]:.3f} '
                  f'unseen class={per_fold[(arm,fi)]["unseen_game_dev"]["three_class_accuracy"]:.3f}',flush=True)
            del net
            if device.startswith('cuda'):torch.cuda.empty_cache()
    results={};paired_rows=[];unmatched_rows=[]
    for arm in selected:
        arm_pairs,unmatched,unseen_all=_assemble_arm_pairs(
            arm,eligibility,groups,computed,dev,labels)
        paired_rows.extend(arm_pairs)
        unmatched_rows.extend({'arm':arm,**r} for r in unmatched)
        reserve=[r for r in arm_pairs if r['reserved_magnitude']]
        regular=[r for r in arm_pairs if not r['reserved_magnitude']]
        metrics=_paired_summary(arm_pairs,seed=seed,permutations=permutations)
        cover=_coverage(arm_pairs,unmatched,unseen_all)
        diagnosis=_interpret_gap(metrics)
        if cover['unmatched_cases']:
            diagnosis['coverage_qualification']='PAIRED_SUBSET_ONLY_UNMATCHED_GAMES_EXCLUDED'
        results[arm]={'diagnosis':diagnosis,
            'paired_same_dev_cases':metrics,'paired_coverage':cover,
            'all_unseen_dev_cases':_unseen_all_metrics(unseen_all),
            'paired_nonreserved_magnitudes':_paired_summary(regular,seed=seed,permutations=permutations),
            'paired_reserved_untrained_magnitudes':_paired_summary(reserve,seed=seed,permutations=permutations),
            'by_fold':[{'fold':i,**per_fold[(arm,i)]} for i in range(len(groups))]}
        if metrics['status']=='COMPLETE_PAIRED_SAME_CERTIFIED_DEV_ROWS':
            print(f'  {arm}: paired={cover["paired_cases"]}/{cover["eligible_cases"]} '
                  f'(unmatched={cover["unmatched_cases"]}); '
                  f'familiar MAE={metrics["familiar_game"]["mae"]:.4f}, '
                  f'unseen matched MAE={metrics["unseen_game"]["mae"]:.4f}; '
                  f'familiar small-neg={metrics["familiar_game"]["small_negative_sign_accuracy"]} '
                  f'unseen small-neg={metrics["unseen_game"]["small_negative_sign_accuracy"]}',flush=True)
        else:
            print(f'  {arm}: BLOCKED_NO_PAIRED_ROWS '
                  f'(all {cover["eligible_cases"]} heldout cases still scored independently)',flush=True)
    for path,sha in initial.items():
        if suff._digest(path)!=sha:raise ValueError('GAMER_GENERALIZATION_SOURCE_MUTATED:'+path)
    out.mkdir(parents=True,exist_ok=False)
    # Separate model-by-model detail from matched-prompt comparisons.
    def write_csv(name,records,empty_fields):
        with (out/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(records[0]) if records else empty_fields)
            writer.writeheader();writer.writerows(records)
    write_csv('all_fold_predictions.csv',out_rows,
              ['arm','fold','dev_case_index','game_id','cohort','target','prediction'])
    write_csv('paired_dev_predictions.csv',paired_rows,
              ['arm','dev_case_index','game_id','target','familiar_fold','unseen_fold'])
    write_csv('unmatched_familiar_dev_cases.csv',unmatched_rows,
              ['arm','dev_case_index','game_id','target','true_class','unseen_fold',
               'validation_folds','reason'])
    summary={'schema':SCHEMA,'status':'COMPLETE_READ_ONLY_NOT_PROMOTED',
        'source_sufficiency':str(root),'source_causal':str(causal),
        'settings':{'device':device,'batch_size':batch_size,'arms':list(selected),
                    'seed':seed,'paired_game_permutations':permutations},
        'certified_dev_unique_cases':len(eligibility),
        'certified_dev_game_count':len({dev[i]['game_id'] for i in eligibility}),
        'class_balance':{CLASS_NAMES[i]:int(np.sum(suff._classes(labels[eligibility])==i)) for i in range(3)},
        'source_sha256':initial,'results':results,
        'paired_coverage_by_arm':{arm:results[arm]['paired_coverage'] for arm in selected},
        'interpretation_constraints':[
           'All scores are on original certified dev prompts, never synthetic register-derived answer labels.',
           'Familiar-game dev prompts are never used to fit, validate, or early-stop the scored model, but other prompts from their game were fitted.',
           'Unseen-game dev prompts are from games entirely absent from the scored model fitting and validation.',
           'Comparisons pair exactly the same dev prompt; familiar and unseen predictions come from independently fitted fold models.',
           'The first familiar model is chosen by fold index before looking at model predictions.',
           'Zero is always a learned training class; zero performance on familiar games is a heldout-prompt test, not unseen-numeric-value verification.',
           'Magnitude-reserved targets were excluded from fitting AND early stopping in every fold; their results contain no true-zero examples.',
           'Validation-game dev prompts are never counted as game-familiar because their games influenced early stopping.',
           'Some dev cases have no valid familiar model: all other folds validated on that game. Such cases are excluded from paired comparison only, explicitly counted, and retained in all_unseen_dev_cases.',
           'The paired comparison may be a selected subset and must not be reported as covering all heldout cases when unmatched_cases is nonzero.',
           'Per-game permutation scores are exploratory and not causal evidence of feature sufficiency.',
           'The localized positions are not established as semantically unique or computationally necessary.',
           'No weight changes, gradients, training, cache writes, LoRA updates, or production promotion occur.',
        ]}
    gamer.atomic_json(out/'final_report.json',summary)
    print('GAMER READOUT GENERALIZATION COMPLETE: '+str(out/'final_report.json'),flush=True)
    return summary


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--output',type=Path)
    ap.add_argument('--device',default='cuda')
    ap.add_argument('--batch-size',type=int,default=64)
    ap.add_argument('--arms',default=','.join(ARM_NAMES))
    ap.add_argument('--seed',type=int,default=47)
    ap.add_argument('--permutations',type=int,default=999)
    a=ap.parse_args()
    run_generalization(a.run,output=a.output,device=a.device,batch_size=a.batch_size,
                       arms=tuple(x.strip() for x in a.arms.split(',')),
                       seed=a.seed,permutations=a.permutations)
