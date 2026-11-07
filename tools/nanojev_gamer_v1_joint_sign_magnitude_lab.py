#!/usr/bin/env python3
"""Read-only GAMER joint lab: dual-path reconstruction and unseen-game transfer.

Compares an immutable signed-offset-only binary sign head against a second
training arm exposed to FOUR auxiliary belief-margin games (never Hawk-Dove).
Neither arm sees the original 72 DEV or 108 TEST prompts during optimization.
Both sign arms reconstruct signed output from the SAME frozen C0 absolute
magnitude. A separate TRAIN/DEV-only nonnegative magnitude calibration is
scored against uncalibrated C0 magnitude and oracle-sign diagnostic references.

Note: Hawk-Dove remains an UNSEEN PAYOFF PROFILE, but the belief-margin prompt
structure is exposed to the cross-game arm. The strict arm sees no belief games.
The label oracle is used only to fit training heads and score diagnostics.
No production LoRA, C0, or original oracle files are changed.

 python -m tools.nanojev_gamer_v1_joint_sign_magnitude_lab \\
  --dual runtime/reports/gamer-v1/c0-dual-sign-0001 \\
  --output runtime/reports/gamer-v1/c0-joint-sign-magnitude-0001 \\
  --steps 3000 --device cuda
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import gc
import json
from pathlib import Path
import numpy as np

from tools import nanojev_gamer_v1_dual_sign_head as dual
from tools import nanojev_gamer_v1_c0_sign_head as three

SCHEMA='nanojev.gamer.v1.joint-frozen-sign-magnitude-transfer.v1'
AUX_GAMES=('battle_leader','stag_hunt','assurance','coordination')
# Distinct from all six original hawk TEST belief offsets, and bounded by q in [0,1].
AUX_EPS=(Fraction(1,1200),Fraction(1,800),Fraction(1,600),
         Fraction(1,400),Fraction(1,300),Fraction(1,150))
STAGE='lora_final_mean100'


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _auxiliary_deck(existing):
    """TRAIN only: other game profiles, with original hawk family untouched."""
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_zero_sign_frob as original
    from tools import nanojev_two_game_spine_smoke as spine
    old=list(existing)
    added=[]
    for game in AUX_GAMES:
        for i,eps in enumerate(AUX_EPS):
            # Change public payoff magnitudes as well as belief offset. Without
            # this, the exact-zero question repeats six times per game.
            stage=spine.ordinal_stage(spine.PAYOFF_ORDERS[game],shift=2*i,scale=i+1)
            q0=spine.critical_q(stage)
            if q0 is None or stage.margin(q0)!=0:
                raise ValueError('GAMER_JOINT_NO_ZERO_CROSSING_IN_AUXILIARY_GAME')
            group=f'train.belief.{game}.{i:04d}'
            for tag,delta in (('negative_q',-eps),('zero_q',Fraction(0)),('positive_q',eps)):
                q=q0+delta
                if not 0<=q<=1:raise ValueError('GAMER_JOINT_AUX_BELIEF_OUT_OF_RANGE')
                answer=stage.margin(q)
                for surface,renderer in (
                    ('spine_belief',original._spine_belief_prompt),
                    ('game_prose_belief',original._game_prose_belief_prompt)):
                    added.append(oracle.Case(
                        f'{group}.{tag}.{surface}','train',group,surface,
                        renderer(stage,q),answer,f'aux_{game}',delta))
    oracle.validate_deck(old+added)
    if len(added)!=144 or len({x.prompt for x in added})!=144:
        raise ValueError('GAMER_JOINT_AUXILIARY_DECK_NOT_COMPLETE')
    old_ids={x.id for x in old}
    old_prompts={oracle.sha_text(x.prompt) for x in old}
    if any(c.id in old_ids or oracle.sha_text(c.prompt) in old_prompts for c in added):
        raise ValueError('GAMER_JOINT_ORIGINAL_HOLDOUT_PROMPT_COLLISION')
    if any(x.family=='hawk_indifference' or x.split!='train' for x in added):
        raise ValueError('GAMER_JOINT_HAWK_TRAIN_LEAKAGE')
    return old+added,added


def _source(dual_dir,steps):
    root=Path(dual_dir).resolve()
    report_path=root/'final_report.json'
    prior=_json(report_path)
    if (prior.get('schema')!=dual.SCHEMA or
        prior.get('status')!='COMPLETE_TWO_BINARY_HEADS_NO_PRODUCTION_UPDATES' or
        prior.get('promotion')!='BLOCKED_DIAGNOSTIC_ONLY' or
        prior.get('no_original_lora_or_production_c0_training') is not True or
        prior.get('dev_and_test_unchanged') is not True):
        raise ValueError('GAMER_JOINT_REQUIRES_COMPLETED_DUAL_AUDIT')
    requested=sorted(set(int(s) for s in steps))
    if not requested or not set(map(str,requested))<=set(prior['steps']):
        raise ValueError('GAMER_JOINT_UNAUDITED_SNAPSHOT_REQUESTED')
    origin=dual._source(Path(prior['source_three_class_report']).parent,requested)
    if (prior['fingerprints']!=dual._fingerprints(origin) or
        prior['original_oracle_dataset_sha256']!=origin['source']['meta']['dataset_sha256'] or
        prior['expanded_training_deck_sha256']!=origin['expanded_deck_sha256'] or
        any(prior['snapshots'].get(str(k))!=v[1] for k,v in origin['source']['snapshots'].items())):
        raise ValueError('GAMER_JOINT_CHANGED_PRIOR_ORACLE_OR_WEIGHTS')
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    deck,added=_auxiliary_deck(origin['deck'])
    extended=[case.public() for case in deck]
    if extended[:len(origin['rows'])]!=origin['rows']:
        raise ValueError('GAMER_JOINT_CHANGED_ORIGINAL_756_CASES')
    split=three._partitions(extended)
    if (len(split['train'])!=720 or len(split['dev'])!=72 or
        len(split['test'])!=108 or sum(extended[int(i)]['family']=='hawk_indifference'
            for i in split['test'])!=36 or
        any(extended[int(i)]['family']=='hawk_indifference' for i in split['train'])):
        raise ValueError('GAMER_JOINT_TRAIN_DEV_TEST_ISOLATION_FAILED')
    for k in requested:
        artifact=prior['steps'][str(k)]['stages'][STAGE]['diagnostic_binary_head_artifact']
        if (artifact['type']!='DIAGNOSTIC_TWO_BINARY_SIGN_HEADS_NO_MAGNITUDE' or
            not Path(artifact['path']).is_file() or
            three._sha(artifact['path'])!=artifact['sha256']):
            raise ValueError('GAMER_JOINT_STRICT_SIGN_ARTIFACT_CHANGED')
    return {'origin':origin,'prior':prior,'report_path':report_path,
            'deck':deck,'rows':extended,'split':split,
            'additional_deck_sha256':oracle.deck_hash(added),
            'combined_deck_sha256':oracle.deck_hash(deck),
            'requested':requested}


def _validate_arrays(rows,classes,magnitude):
    cls=np.asarray(classes,dtype=np.int64)
    mag=np.asarray(magnitude,dtype=np.float64)
    if (cls.shape!=(len(rows),) or mag.shape!=(len(rows),) or
        np.any((cls<0)|(cls>2)) or not np.isfinite(mag).all() or np.any(mag<0)):
        raise ValueError('GAMER_JOINT_BAD_SIGN_OR_MAGNITUDE_ARRAY')
    return cls,mag


def _magnitude_fit(rows,indices,predictions):
    """Train-only magnitude fits; DEV chooses raw, gain, or nonnegative affine."""
    idx=np.asarray(indices,dtype=int)
    x=np.asarray(predictions,dtype=np.float64)[idx]
    y=np.asarray([abs(float(Fraction(rows[int(i)]['target']))) for i in idx],dtype=np.float64)
    if not len(idx) or not np.isfinite(x).all() or not np.isfinite(y).all() or np.any(x<0):
        raise ValueError('GAMER_JOINT_BAD_MAGNITUDE_TRAIN')
    gain=max(0.,float((x@y)/(x@x+1e-9)))
    cov=np.asarray(np.linalg.lstsq(np.column_stack((x,np.ones(len(x)))),y,rcond=None)[0])
    slope=max(0.,float(cov[0]));intercept=max(0.,float(cov[1]))
    return {'raw':(1.,0.),'gain':(gain,0.),'affine':(slope,intercept)}


def _magnitude_apply(x,coef):
    a,b=coef
    return np.maximum(0.,a*np.asarray(x,dtype=np.float64)+b)


def _magnitude_errors(rows,idx,magnitude):
    ix=np.asarray(idx,dtype=int)
    raw=np.asarray(magnitude,dtype=np.float64)
    y=np.asarray([float(Fraction(rows[int(i)]['target'])) for i in ix],dtype=np.float64)
    m=raw[ix]
    if not np.isfinite(m).all() or np.any(m<0):
        raise ValueError('GAMER_JOINT_NONFINITE_OR_NEGATIVE_MAGNITUDE')
    e=np.abs(m-np.abs(y))
    def score(keep):
        err=e[keep];truth=y[keep];p=m[keep]
        return {'cases':int(len(err)),'magnitude_mae':float(np.mean(err)),
                'magnitude_p90_abs_error':float(np.quantile(err,.9)),
                'mean_estimated_magnitude':float(np.mean(p)),
                'zero_target_magnitude_mean':float(np.mean(p[truth==0])) if np.any(truth==0) else None}
    family={}
    for name in sorted({rows[int(i)]['family'] for i in ix}):
        chosen=np.asarray([j for j,i in enumerate(ix) if rows[int(i)]['family']==name],dtype=int)
        family[name]=score(chosen)
    classes={str(sign):score(np.flatnonzero(np.sign(y)==sign)) for sign in (-1,0,1)}
    return {'all':score(np.arange(len(ix))),'by_family':family,'by_true_sign':classes}


def _select_magnitude(rows,split,raw_magnitude,train_idx):
    coeff=_magnitude_fit(rows,train_idx,raw_magnitude)
    dev=np.asarray(split['dev'],dtype=int)
    target=np.asarray([abs(float(Fraction(rows[int(i)]['target']))) for i in dev])
    ranked=[]
    for name,ab in coeff.items():
        m=_magnitude_apply(np.asarray(raw_magnitude)[dev],ab)
        mae=float(np.mean(np.abs(m-target)))
        ranked.append((mae,('raw','gain','affine').index(name),name,ab))
    _,_,name,ab=min(ranked)
    return {'name':name,'slope':ab[0],'intercept':ab[1],
            'dev_mae_by_method':{item[2]:item[0] for item in ranked},
            'train_cases':len(train_idx),'selection':'DEV_ONLY_NO_TEST_SELECTION'}


def _reconstruction(rows,idx,cls,magnitude,actual_production):
    """Per-case signed recombination; oracle sign is an analysis-only comparator."""
    idx=np.asarray(idx,dtype=int)
    klass,mag=_validate_arrays(rows,cls,magnitude)
    signs=klass-1
    target=np.asarray([float(Fraction(rows[i]['target'])) for i in range(len(rows))],dtype=np.float64)
    original=np.asarray(actual_production,dtype=np.float64)
    if original.shape!=target.shape or not np.isfinite(original).all():
        raise ValueError('GAMER_JOINT_BAD_FROZEN_C0_PREDICTIONS')
    restored=signs*mag
    oracle_signed=np.sign(target)*mag
    oracle_magnitude=signs*np.abs(target)
    def scored(test_ix):
        t=target[test_ix];p=restored[test_ix];oracle_s=oracle_signed[test_ix]
        org=original[test_ix];oracle_m=oracle_magnitude[test_ix]
        exact_zero=np.flatnonzero(t==0)
        return {
            'cases':len(test_ix),
            'combined_mae':float(np.mean(np.abs(p-t))),
            'frozen_production_c0_mae':float(np.mean(np.abs(org-t))),
            'oracle_sign_frozen_magnitude_mae_reference':float(np.mean(np.abs(oracle_s-t))),
            'predicted_sign_oracle_magnitude_mae_reference':float(np.mean(np.abs(oracle_m-t))),
            'exact_zero_output_count':int(np.count_nonzero(p[exact_zero]==0)),
            'true_zero_cases':int(len(exact_zero)),
            'false_exact_zero_on_nonzero_count':int(np.count_nonzero(
                (p==0)&(t!=0))),
            'reconstructed_sign_correct_count':int(np.count_nonzero(
                signs[test_ix]==np.sign(t))),
        }
    fam={}
    for name in sorted({rows[int(i)]['family'] for i in idx}):
        keep=np.asarray([i for i in idx if rows[int(i)]['family']==name],dtype=int)
        fam[name]=scored(keep)
    return {'all':scored(idx),'by_family':fam,
            'zero_class_is_exactly_zero_by_construction':True,
            'oracle_sign_and_oracle_magnitude_are_diagnostic_reference_only':True}


def _sum_step(result):
    recap={}
    for step,data in result['steps'].items():
        recap[step]={'strict_sign':data['strict_signed_offset_only']['test']['balanced_sign_accuracy'],
                     'cross_game_sign':data['cross_game_training']['test']['balanced_sign_accuracy'],
                     'strict_heldout_hawk':data['strict_signed_offset_only']['test']['by_family']['hawk_indifference']['balanced_sign_accuracy'],
                     'cross_game_heldout_hawk':data['cross_game_training']['test']['by_family']['hawk_indifference']['balanced_sign_accuracy'],
                     'strict_hawk_triples':data['strict_signed_offset_only']['test']['by_family']['hawk_indifference']['complete_triples'],
                     'cross_game_hawk_triples':data['cross_game_training']['test']['by_family']['hawk_indifference']['complete_triples'],
                     'frozen_c0_magnitude_mae':data['magnitude']['uncalibrated']['all']['magnitude_mae'],
                     'strict_reconstruction_mae':data['reconstructions']['strict_uncalibrated']['all']['combined_mae'],
                     'cross_game_reconstruction_mae':data['reconstructions']['cross_game_uncalibrated']['all']['combined_mae']}
    return {'steps':recap,'no_promotion':True,'cross_game_arm_exposes_related_belief_games_but_not_hawk_profile':True,
            'strict_arm_never_sees_any_belief_game':True}


def run(args):
    import torch
    from tools import nanojev_gamer_v1_sign_information_probe as oracle
    from tools import nanojev_gamer_v1_c0_sign_causality as causal
    from tools import nanojev_gamer_v1_c0_boundary as c0
    from tools import nanojev_gamer_v1_lora_sign_stress as stress
    from tools import nanojev_gamer_v1_handoff as gamer

    src=_source(args.dual,args.steps)
    origin=src['origin']['source'];nested=src['origin'];meta=origin['meta']
    protected=[src['report_path'].parent,nested['root'],origin['training'],origin['source'],
               origin['capture'],origin['ckpt'].parent,origin['adapter'],
               origin['audit_path'].parent,origin['causal_path'].parent]
    out=three._output(args.output,protected)
    fingerprints={'dual_report_sha256':three._sha(src['report_path']),
                  **dual._fingerprints(nested)}
    baseline=dict(fingerprints)
    # Initial baseline real-model C0 checkpoint parity (without Qwen loaded).
    net=c0._load_head(meta,args.device)
    raw=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float32)
    _,_,_,original=causal._read_stages(net,raw,device=args.device,batch=args.head_batch)
    c0._verify_baseline(original,origin['archive']['production_head_predictions'])
    del net,raw,original
    gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    rows=src['rows'];split=src['split'];test=split['test']
    result={'schema':SCHEMA,'status':'INCOMPLETE',
        'source_dual_report':str(src['report_path']),'fingerprints':baseline,
        'original_expanded_deck_sha256':nested['expanded_deck_sha256'],
        'additional_auxiliary_train_deck_sha256':src['additional_deck_sha256'],
        'combined_deck_sha256':src['combined_deck_sha256'],
        'new_training_profiles':list(AUX_GAMES),'hawk_test_profile_trained':False,
        'unseen_hawk_payoff_profile_not_unseen_belief_prompt_format':True,
        'original_72_dev_and_108_test_unchanged':True,'train_cases':720,
        'original_signed_offset_only_train_cases':576,
        'new_related_game_train_cases':144,
        'no_production_lora_or_c0_training':True,
        'no_magnitude_model_weights_changed':True,
        'promotion':'BLOCKED_DIAGNOSTIC_ONLY',
        'steps':{},'settings':{'epochs':args.epochs,'nulls':args.nulls,
                               'triplet_weight':args.triplet_weight,
                               'seed':args.seed}}
    out.mkdir(parents=True,exist_ok=False)
    for step in src['requested']:
        snapshot,sha=origin['snapshots'][step]
        if stress._verify_snapshot(snapshot,step)!=sha:
            raise ValueError('GAMER_JOINT_CHANGED_LORA_SNAPSHOT')
        print(f'GAMER JOINT SIGN+MAGNITUDE: step {step}, 720 TRAIN / 72 DEV / 108 TEST',flush=True)
        _,tok,model=gamer.model_components(meta['lineage']['model_id'],args.device,adapter_dir=snapshot)
        features,_=oracle._capture_profiles(torch,model,tok,src['deck'],
            device=args.device,max_length=meta['lineage']['configuration']['max_length'],
            batch_size=args.capture_batch,curvature=meta['lineage']['configuration']['curvature'],
            verify_exact_final=True)
        del model,tok
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        final=np.asarray(features['final_mean100'],dtype=np.float32)
        x=np.asarray(features['final_flat'],dtype=np.float16).astype(np.float32)
        if step==0:
            old=np.asarray(origin['archive']['lora__final_flat'],dtype=np.float16).astype(np.float32)
            if old.shape!=x[nested['old_indices']].shape or \
               np.max(np.abs(old-x[nested['old_indices']]))>.01:
                raise ValueError('GAMER_JOINT_BASELINE_CAPTURE_CHANGED')
        del features
        net=c0._load_head(meta,args.device)
        _,_,_,prediction=causal._read_stages(net,x,device=args.device,batch=args.head_batch)
        del net,x
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()
        # Prior original 324 case replay, including TEST, must match exactly.
        old_metrics=stress._head_metrics(origin['rows'],prediction[nested['old_indices']])['test']
        known=origin['audit']['steps'][str(step)]['metrics']['production_c0']['test']
        if (abs(old_metrics['balanced_sign_accuracy']-known['balanced_sign_accuracy'])>1e-10 or
            old_metrics['triple_all_correct']!=known['triple_all_correct'] or
            abs(old_metrics['signed_mae']-known['signed_mae'])>.001):
            raise ValueError('GAMER_JOINT_FROZEN_C0_PARITY_FAILED')
        # Existing strict arm is REPLAYED, never retrained or retuned on TEST.
        strict_saved=src['prior']['steps'][str(step)]['stages'][STAGE]
        strict_path=strict_saved['diagnostic_binary_head_artifact']['path']
        strict_cls,strict_prob=dual._reload(strict_path,final[test])
        strict_met=three._metrics_by_family(rows,test,strict_cls,strict_prob)
        old_test=strict_saved['test']
        if (strict_met['complete_triples']!=old_test['complete_triples'] or
            abs(strict_met['balanced_sign_accuracy']-old_test['balanced_sign_accuracy'])>1e-10):
            raise ValueError('GAMER_JOINT_STRICT_SIGN_REPLAY_FAILED')
        print('  strict sign (576 offset cases), replayed',flush=True)
        expanded=dual._fit_stage(rows,final,epochs=args.epochs,nulls=args.nulls,
            seed=args.seed,triple_weight=args.triplet_weight,
            output=out/f'step-{step:07d}-cross-game-dual-sign.npz')
        # TEST only scored after TRAIN+DEV selection, no endpoint tuning.
        exposed_cls,exposed_prob=dual._reload(
            expanded['diagnostic_binary_head_artifact']['path'],final[test])
        exposed_met=three._metrics_by_family(rows,test,exposed_cls,exposed_prob)
        if (exposed_met['complete_triples']!=expanded['test']['complete_triples'] or
            exposed_met['balanced_sign_accuracy']!=expanded['test']['balanced_sign_accuracy']):
            raise ValueError('GAMER_JOINT_CROSS_GAME_HEAD_REPLAY_FAILED')
        # Magnitude is evaluated independently of either sign head.
        raw_mag=np.abs(np.asarray(prediction,dtype=np.float64))
        strict_train=np.asarray([i for i in split['train'] if rows[int(i)]['family']=='signed_offset'],dtype=int)
        strict_cal=_select_magnitude(rows,split,raw_mag,strict_train)
        exposed_cal=_select_magnitude(rows,split,raw_mag,split['train'])
        strict_mag=_magnitude_apply(raw_mag,(strict_cal['slope'],strict_cal['intercept']))
        exposed_mag=_magnitude_apply(raw_mag,(exposed_cal['slope'],exposed_cal['intercept']))
        full_strict=np.ones(len(rows),dtype=np.int64)
        full_exposed=np.ones(len(rows),dtype=np.int64)
        full_strict[test]=strict_cls;full_exposed[test]=exposed_cls
        magnitude={'uncalibrated':_magnitude_errors(rows,test,raw_mag),
                   'strict_train_dev_calibration':strict_cal,
                   'cross_game_train_dev_calibration':exposed_cal,
                   'strict_calibrated':_magnitude_errors(rows,test,strict_mag),
                   'cross_game_calibrated':_magnitude_errors(rows,test,exposed_mag)}
        reconstructed={
            'strict_uncalibrated':_reconstruction(rows,test,full_strict,raw_mag,prediction),
            'cross_game_uncalibrated':_reconstruction(rows,test,full_exposed,raw_mag,prediction),
            'strict_calibrated':_reconstruction(rows,test,full_strict,strict_mag,prediction),
            'cross_game_calibrated':_reconstruction(rows,test,full_exposed,exposed_mag,prediction)}
        result['steps'][str(step)]={
            'snapshot_sha256':sha,
            'strict_signed_offset_only':{'test':strict_met,
                'sign_head_checkpoint_sha256':strict_saved['diagnostic_binary_head_artifact']['sha256'],
                'training_cases':576,'related_belief_games_in_train':0},
            'cross_game_training':expanded,
            'magnitude':magnitude,
            'reconstructions':reconstructed,
            'production_frozen_c0_on_original_108':old_metrics,
            'test_case_indices_unchanged':True}
        print('  hawk heldout sign: strict %.3f, cross-game %.3f; triples: %d -> %d' % (
             strict_met['by_family']['hawk_indifference']['balanced_sign_accuracy'],
             exposed_met['by_family']['hawk_indifference']['balanced_sign_accuracy'],
             strict_met['by_family']['hawk_indifference']['complete_triples'],
             exposed_met['by_family']['hawk_indifference']['complete_triples']),flush=True)
        print('  MAE (108): frozen C0 %.4f, recombined strict %.4f, cross-game %.4f' % (
            reconstructed['strict_uncalibrated']['all']['frozen_production_c0_mae'],
            reconstructed['strict_uncalibrated']['all']['combined_mae'],
            reconstructed['cross_game_uncalibrated']['all']['combined_mae']),flush=True)
        gamer.atomic_json(out/f'step-{step:07d}.json',result['steps'][str(step)])
        gamer.atomic_json(out/'progress.json',{'status':'IN_PROGRESS','steps':sorted(result['steps'])})
        del final,prediction,expanded
        gc.collect()
    if ({'dual_report_sha256':three._sha(src['report_path']),
         **dual._fingerprints(nested)}!=baseline):
        raise ValueError('GAMER_JOINT_PROTECTED_ORIGINAL_SOURCES_MUTATED')
    result['trajectory_summary']=_sum_step(result)
    result['status']='COMPLETE_FROZEN_DUAL_PATH_AND_UNSEEN_PROFILE_TRANSFER'
    result['interpretation']=(
        'Strict sign head is replayed from signed-offset-only TRAIN; cross-game arm '
        'trains on other game belief profiles but never Hawk-Dove; heldout hawk '
        'remains an unseen payoff profile, NOT an unseen belief-format family. '
        'C0 absolute magnitude is checked independently with frozen, TRAIN/DEV '
        'calibration. Oracle-sign upper bounds are ANALYSIS ONLY. TEST never '
        'selects classifiers, calibrators, checkpoints, or thresholds. '
        'No production promotion or magnitude certification without retention.')
    gamer.atomic_json(out/'final_report.json',result)
    print('GAMER JOINT COMPLETE: '+str(out/'final_report.json'),flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dual',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--steps',default='3000')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--capture-batch',type=int,default=2)
    parser.add_argument('--head-batch',type=int,default=8)
    parser.add_argument('--epochs',type=int,default=120)
    parser.add_argument('--nulls',type=int,default=2)
    parser.add_argument('--triplet-weight',type=float,default=.25)
    parser.add_argument('--seed',type=int,default=47)
    args=parser.parse_args(argv)
    try:args.steps=sorted({int(x.strip()) for x in args.steps.split(',') if x.strip()})
    except ValueError:raise ValueError('GAMER_JOINT_BAD_STEPS') from None
    if (not args.steps or min(args.steps)<0 or args.capture_batch<1 or
        args.head_batch<1 or not 10<=args.epochs<=1000 or
        not 1<=args.nulls<=10 or not 0<=args.triplet_weight<=10):
        raise ValueError('GAMER_JOINT_BAD_FLAGS')
    return run(args)


if __name__=='__main__':main()
