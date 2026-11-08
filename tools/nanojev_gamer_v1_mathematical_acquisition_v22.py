#!/usr/bin/env python3
"""V22: matched mathematical acquisition from V14, V19 and V21 checkpoints.

All three arms share one architecture, seed-specific training schedules, losses,
projection and exact TRAIN sign-retention gates. DEV only selects checkpoints.
Original-task passthrough remains capture-bound and frozen. Historical V19/V21
weights are read-only research inputs; no TEST is accessed or promoted.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from tools import nanojev_gamer_v1_shadow_precision_v21 as v21
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_precision_resolution_v20 as v20
from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA='nanojev.gamer.v1.matched-acquisition.v22'
ARMS=('v14_post_consolidation','v19_precision_joint','v21_shadow_precision')


def _seeds(value):
    try:
        out=[int(x.strip()) for x in str(value).split(',')]
    except (TypeError,ValueError) as exc:
        raise argparse.ArgumentTypeError('V22_BAD_SEEDS') from exc
    if not 1<=len(out)<=6 or len(set(out))!=len(out) or any(x<0 or x>=2**31 for x in out):
        raise argparse.ArgumentTypeError('V22_SEEDS_REQUIRE_1_TO_6_UNIQUE_NONNEGATIVE')
    return out


def _validate(args,torch):
    required=('run','expanded_run','input','source_features','prior',
              'v11_run','v14_run','v19_run','v20_run','v21_run','output')
    if any(getattr(args,k,None) is None for k in required):
        raise ValueError('V22_MISSING_REQUIRED_INPUT')
    if (not 1<=args.steps<=2000 or not 1<=args.eval_every<=args.steps or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.replay_weight) or args.replay_weight<0 or
        not 0<=args.max_halvings<=12 or not 1<=args.projection_passes<=20 or
        not 1<=args.max_constraints<=128 or not isinstance(args.seeds,list) or
        not 1<=len(args.seeds)<=6 or len(set(args.seeds))!=len(args.seeds) or
        any(not isinstance(n,int) or n<0 or n>=2**31 for n in args.seeds)):
        raise ValueError('V22_INVALID_CONFIGURATION')
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise ValueError('V22_CUDA_NOT_AVAILABLE')
    return required


def _metadata_check(r19,r20,r21,*,sha19,sha20,sha19cp,sha19trials,
                    capture,prior_hash,proof,verified14,lineage):
    """Cross-file provenance before loading any later checkpoint as authority."""
    # Enumerate provenance failures instead of hiding them behind one OR expression.
    # Every predicate from the original gate remains enforced.
    r21_summary=r21.get('summary')
    if not isinstance(r21_summary,dict):
        r21_summary={}
    checks={
        'v19.schema':r19.get('schema')==v19.SCHEMA,
        'v19.status':r19.get('status')=='EXPERIMENT_COMPLETE_NO_PROMOTION',
        'v19.checkpoint_saved':r19.get('checkpoint_saved') is True,
        'v19.source_anchor':r19.get('source_anchor')=='VERIFIED_V14_POST_CONSOLIDATION_NOT_V18',
        'v19.training_capture_sha256':r19.get('training_capture_sha256')==capture,
        'v19.v6_prior_sha256':r19.get('v6_prior_sha256')==prior_hash,
        'v19.v11_verified':r19.get('v11_verified')==proof,
        'v19.v14_verified':r19.get('v14_verified')==verified14,
        'v19.frozen_lineage':r19.get('frozen_lineage')==lineage,
        'v19.production_weights_modified':r19.get('production_weights_modified') is False,
        'v19.promotion_authorized':r19.get('promotion_authorized') is False,
        'v19.synthetic_dev_used_for_training_or_selection':r19.get('synthetic_dev_used_for_training_or_selection') is False,
        'v19.previously_seen_test_used_for_training_or_selection':r19.get('previously_seen_test_used_for_training_or_selection') is False,
        'v20.frozen_lineage':r20.get('frozen_lineage')==lineage,
        'v21.schema':r21.get('schema')==v21.SCHEMA,
        'v21.status':r21.get('status')=='EXPERIMENT_COMPLETE_NO_PROMOTION',
        'v21.checkpoint_saved':r21.get('checkpoint_saved') is True,
        'v21.summary.float32_export_updates':(
            isinstance(r21_summary.get('float32_export_updates'),int)
            and not isinstance(r21_summary.get('float32_export_updates'),bool)
            and r21_summary['float32_export_updates']>=1),
        'v21.summary.checkpoint_saved':r21_summary.get('checkpoint_saved') is True,
        'v21.source_anchor':r21.get('source_anchor')=='VERIFIED_V14_POST_CONSOLIDATION',
        'v21.training_capture_sha256':r21.get('training_capture_sha256')==capture,
        'v21.v6_prior_sha256':r21.get('v6_prior_sha256')==prior_hash,
        'v21.v11_verified':r21.get('v11_verified')==proof,
        'v21.v14_verified':r21.get('v14_verified')==verified14,
        'v21.frozen_lineage':r21.get('frozen_lineage')==lineage,
        'v21.v19_report_sha256':r21.get('v19_report_sha256')==sha19,
        'v21.v19_trials_sha256':r21.get('v19_trials_sha256')==sha19trials,
        'v21.v19_checkpoint_sha256':r21.get('v19_checkpoint_sha256')==sha19cp,
        'v21.v20_report_sha256':r21.get('v20_report_sha256')==sha20,
        'v21.production_weights_modified':r21.get('production_weights_modified') is False,
        'v21.promotion_authorized':r21.get('promotion_authorized') is False,
        'v21.synthetic_dev_used_for_training_or_selection':r21.get('synthetic_dev_used_for_training_or_selection') is False,
        'v21.previously_seen_test_used_for_training_or_selection':r21.get('previously_seen_test_used_for_training_or_selection') is False,
        'v21.hard_train_retention':r21.get('hard_train_retention')=='UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
    }
    failed=[label for label,passed in checks.items() if not passed]
    if failed:
        raise ValueError('V22_HISTORICAL_PROVENANCE_OR_HASH_MISMATCH: '+', '.join(failed))
    v21._validate_v20(r20,r19,sha19,sha19trials,sha19cp)
    return True


def _checkpoint_mask(torch,cp,*,arm,expected_capture,expected_anchor,
                     lineage,width,capacity_width,mu,sd):
    import numpy as np
    if arm==ARMS[1]:
        ok=(cp.get('schema')==v19.SCHEMA and
            cp.get('v14_post_sha256')==expected_anchor and
            cp.get('protected_original_passthrough') is True and
            cp.get('hard_full_train_retention') is True)
    elif arm==ARMS[2]:
        ok=(cp.get('schema')==v21.SCHEMA and
            cp.get('v14_post_sha256')==expected_anchor and
            cp.get('protected_original_passthrough') is True and
            cp.get('hard_full_train_retention') is True)
    else:
        raise ValueError('V22_UNKNOWN_SOURCE_ARM')
    if (not ok or cp.get('status')!='DISPOSABLE_NON_PRODUCTION' or
        cp.get('promotion_authorized') is not False or
        cp.get('training_capture_sha256')!=expected_capture or
        cp.get('frozen_lineage')!=lineage or
        cp.get('margin_width')!=width or
        cp.get('capacity_width')!=capacity_width or
        not np.array_equal(cp['mean'].numpy(),mu) or
        not np.array_equal(cp['std'].numpy(),sd)):
        raise ValueError('V22_SOURCE_CHECKPOINT_PROVENANCE_'+arm)
    if not all(torch.is_tensor(t) and t.dtype==torch.float32 and
               bool(torch.isfinite(t).all()) for t in cp['weights'].values()
               if t.is_floating_point()):
        raise ValueError('V22_SOURCE_STATE_PRECISION_'+arm)
    return cp['train_ledger_mask'].cpu().numpy().astype(bool)


def _verified_starts(torch,args,data,spec,mu,sd,capture,proof,verified14,report14,post,model):
    """Require actual inference to match each immutable checkpoint TRAIN mask."""
    import numpy as np
    p19=Path(args.v19_run);p20=Path(args.v20_run);p21=Path(args.v21_run)
    f19=p19/'report.json'; f20=p20/'report.json'; f21=p21/'report.json'
    t19=p19/'precision_joint_trials.csv'
    c19=p19/'precision_joint_disposable_candidate.pt'
    c21=p21/'shadow_precision_disposable_candidate.pt'
    for path in (f19,f20,f21,t19,c19,c21):
        if not path.is_file():
            raise ValueError('V22_REQUIRED_HISTORY_FILE_MISSING_'+path.name)
    r19=json.loads(f19.read_text(encoding='utf-8'))
    r20=json.loads(f20.read_text(encoding='utf-8'))
    r21=json.loads(f21.read_text(encoding='utf-8'))
    sha19=v19._sha(f19);sha20=v19._sha(f20);sha21=v19._sha(f21)
    sha19cp=v19._sha(c19);sha19trials=v19._sha(t19)
    lineage=data['probe_info']['lineage'];prior_hash=v19._sha(args.prior)
    _metadata_check(r19,r20,r21,sha19=sha19,sha20=sha20,
        sha19cp=sha19cp,sha19trials=sha19trials,capture=capture,
        prior_hash=prior_hash,proof=proof,verified14=verified14,lineage=lineage)
    cp19=torch.load(c19,map_location='cpu',weights_only=True)
    cp21=torch.load(c21,map_location='cpu',weights_only=True)
    if (cp21.get('v19_checkpoint_sha256')!=sha19cp or
        cp21.get('v20_report_sha256')!=sha20 or
        cp19.get('v18_checkpoint_sha256')!=r19.get('reference_v18',{}).get('v18_checkpoint_sha256')):
        raise ValueError('V22_CHECKPOINT_HISTORICAL_LINK_BROKEN')
    width=report14['config']['margin_width'];capacity=report14['config']['capacity_width']
    start_correct=v9._correct_train(torch,model,spec,mu,sd,args.device)
    if not np.array_equal(start_correct,post['train_ledger_mask'].cpu().numpy().astype(bool)):
        raise ValueError('V22_V14_SOURCE_LEDGER_DRIFT')
    starts={ARMS[0]:{k:v.detach().cpu().clone() for k,v in post['weights'].items()}}
    audit=[]
    freeze_ref={k:v.clone() for k,v in starts[ARMS[0]].items()
                if k.startswith('base.base.')}
    for arm,cp,path in ((ARMS[1],cp19,c19),(ARMS[2],cp21,c21)):
        expected=_checkpoint_mask(torch,cp,arm=arm,expected_capture=capture,
            expected_anchor=verified14['v14_post_consolidation_sha256'],
            lineage=lineage,width=width,capacity_width=capacity,mu=mu,sd=sd)
        state=cp['weights']
        if (state.keys()!=starts[ARMS[0]].keys() or
            any(not torch.equal(state[k].cpu(),value) for k,value in freeze_ref.items()) or
            not np.array_equal(expected,start_correct)):
            raise ValueError('V22_UNMATCHED_ARCHITECTURE_FREEZE_OR_INITIAL_COMPETENCE_'+arm)
        model.load_state_dict(state,strict=True);model.eval()
        observed=v9._correct_train(torch,model,spec,mu,sd,args.device)
        ret,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
        if not np.array_equal(observed,expected) or ret['status']!='PASS':
            raise ValueError('V22_CHECKPOINT_ACTUAL_INFERENCE_RETENTION_'+arm)
        starts[arm]={k:v.detach().cpu().clone() for k,v in state.items()}
        atlas=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
        audit.append({'arm':arm,'checkpoint_sha256':v19._sha(path),
                      'verified_train_correct':int(observed.sum()),
                      **_atlas_metrics(atlas),
                      'dev_sign':dev['all']['balanced_sign_accuracy'],
                      'dev_zero_recall':dev['all']['zero_recall'],
                      'original_retention':ret['status']})
    model.load_state_dict(starts[ARMS[0]],strict=True);model.eval()
    ret,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
    if ret['status']!='PASS':
        raise ValueError('V22_V14_ORIGINAL_RETENTION_FAIL')
    atlas=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
    audit.insert(0,{'arm':ARMS[0],
        'checkpoint_sha256':verified14['v14_post_consolidation_sha256'],
        'verified_train_correct':int(start_correct.sum()),
        **_atlas_metrics(atlas),
        'dev_sign':dev['all']['balanced_sign_accuracy'],
        'dev_zero_recall':dev['all']['zero_recall'],
        'original_retention':ret['status']})
    return starts,audit,{'v19_report_sha256':sha19,
        'v19_checkpoint_sha256':sha19cp,'v19_trials_sha256':sha19trials,
        'v20_report_sha256':sha20,'v21_report_sha256':sha21,
        'v21_checkpoint_sha256':v19._sha(c21)}


def _atlas_metrics(cases):
    grouped=v7._group_atlas(cases)
    total={n:0 for n in (-1,0,1)};correct={n:0 for n in total}
    for case in cases:
        t=int(case['target_sign']);total[t]+=1;correct[t]+=int(case['correct'])
    return {'dev_correct':sum(c['correct'] for c in cases),
        'dev_total':len(cases),'dev_complete_six':sum(g['complete_six'] for g in grouped),
        'dev_complete_triples':sum(g['left_complete_triple']+g['right_complete_triple'] for g in grouped),
        'zero_correct':correct[0],'zero_total':total[0],
        'negative_correct':correct[-1],'negative_total':total[-1],
        'positive_correct':correct[1],'positive_total':total[1],
        'zero_misses':sum(c['zero_miss'] for c in cases),
        'nonzero_as_zero':sum(c['nonzero_as_zero'] for c in cases),
        'wrong_direction':sum(c['wrong_direction'] for c in cases),
        'reversal_miss_groups':sum(g['reversal_miss'] for g in grouped),
        'surface_disagreement_pairs':sum(g['surface_disagreement_pairs'] for g in grouped)}


def _snapshot_metrics(torch,model,snapshot,spec,mu,sd,device,baseline_cases,
                      arm,seed,state,initial_mask,groups):
    import numpy as np
    model.load_state_dict(snapshot['weights'],strict=True);model.eval()
    seen=v9._correct_train(torch,model,spec,mu,sd,device)
    if (not np.array_equal(seen,snapshot['ledger_mask']) or
        not np.array_equal(seen,snapshot['acquired_step']>=0) or
        not bool(seen[initial_mask].all())):
        raise ValueError('V22_CHECKPOINT_FAILED_CUMULATIVE_TRAIN_RETENTION')
    ret,dev=v6._evaluate(torch,model,spec,mu,sd,device)
    if (ret['status']!='PASS' or
        dev['all']['balanced_sign_accuracy']!=snapshot['dev']['all']['balanced_sign_accuracy'] or
        dev['cross_format']['complete_six_case_groups']!=snapshot['dev']['cross_format']['complete_six_case_groups']):
        raise ValueError('V22_DEVELOPMENT_OR_ORIGINAL_RETENTION_DRIFT')
    atlas=v7._case_atlas(torch,model,spec,mu,sd,device,'dev')
    changed_cases,changed_groups=v11._dev_transitions(baseline_cases,atlas,arm,state)
    for row in changed_cases:row['seed']=seed
    for row in changed_groups:row['seed']=seed
    acquired=v9._memory_summary(seen,groups)
    before=v9._memory_summary(initial_mask,groups)
    metrics=_atlas_metrics(atlas)
    if metrics['dev_complete_six']!=dev['cross_format']['complete_six_case_groups']:
        raise ValueError('V22_ATLAS_DEV_GROUP_SCORE_MISMATCH')
    row={'arm':arm,'seed':seed,'state':state,'step':snapshot['step'],
         **metrics,'dev_sign':dev['all']['balanced_sign_accuracy'],
         'dev_zero_recall':dev['all']['zero_recall'],
         'train_correct':acquired['correct_cases'],
         'train_gain':acquired['correct_cases']-before['correct_cases'],
         'train_six':acquired['complete_six_case_groups'],
         'train_six_gain':acquired['complete_six_case_groups']-before['complete_six_case_groups'],
         'train_triples':acquired['complete_triples'],
         'dev_gained_cases':sum(x['transition']=='newly_correct' for x in changed_cases),
         'dev_lost_cases':sum(x['transition']=='lost_correct' for x in changed_cases),
         'dev_gained_groups':sum(x['new_complete'] for x in changed_groups),
         'dev_lost_groups':sum(x['lost_complete'] for x in changed_groups),
         'retention':'PASS','promotion_authorized':False}
    return row,changed_cases,changed_groups


def _matched_pair(rows,seed,stage):
    entries={r['arm']:r for r in rows if r['seed']==seed and r['state']==stage}
    if set(entries)!=set(ARMS):
        raise ValueError('V22_INCOMPLETE_MATCHED_COMPARISON')
    control=entries[ARMS[0]]
    return [{'seed':seed,'state':stage,'arm':arm,
        **{'delta_'+field:entries[arm][field]-control[field] for field in
          ('dev_correct','dev_complete_six','dev_complete_triples',
           'zero_correct','negative_correct','positive_correct',
           'dev_lost_cases','dev_lost_groups','train_correct','train_six','train_triples')},
        'delta_updates_accepted':None} for arm in ARMS[1:]]


def run(args):
    import numpy as np
    import torch
    _validate(args,torch)
    source_names=('run','expanded_run','input','source_features','prior',
                  'v11_run','v14_run','v19_run','v20_run','v21_run')
    out=lab._output(args.output,[Path(getattr(args,n)) for n in source_names])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    _,cp11,_,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                  v19._sha(args.prior),spec,args.device)
    if not (np.array_equal(cp11['selected']['mean'].numpy(),mu) and
            np.array_equal(cp11['selected']['std'].numpy(),sd)):
        raise ValueError('V22_NORMALIZER_CHANGED')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
        v19._sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    starts,audit,provenance=_verified_starts(torch,args,data,spec,mu,sd,capture,
                  proof,verified14,report14,post,model)
    width=report14['config']['margin_width'];capacity=report14['config']['capacity_width']
    tx,old,y,rows,routes=spec['train_synthetic']
    reference_train=v9._correct_train(torch,model,spec,mu,sd,args.device)
    initial_atlases={}
    for arm in ARMS:
        model.load_state_dict(starts[arm],strict=True)
        initial_atlases[arm]=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
    model.load_state_dict(starts[ARMS[0]],strict=True)
    comparison=[];case_changes=[];group_changes=[];pairwise=[];events=[];progress=[];saved={}
    for seed in args.seeds:
        for arm in ARMS:
            builder=lambda t:v14._acquisition_model(t,width,capacity)
            trained,best,ledger,attempts,victims,history,stats=v10._fit_arm(
                torch,spec,starts[arm],mu,sd,arm='expanded_capacity_projected',
                device=args.device,steps=args.steps,eval_every=args.eval_every,
                lr=args.lr,seed=seed,replay_weight=args.replay_weight,
                max_halvings=args.max_halvings,projection_passes=args.projection_passes,
                max_constraints=args.max_constraints,model_builder=builder,
                capture_terminal=True)
            terminal=stats.pop('terminal_snapshot')
            armdata={'seed':seed,'arm':arm,'accepted_updates':stats['accepted_updates'],
                     'projection_rescues':stats['projection_rescues'],
                     'rejected_updates':stats['rejected_updates'],
                     'initial_correct':int(reference_train.sum()),
                     'starting_checkpoint_correct':int(ledger.initial['correct_cases']),
                     'terminal_correct':int(stats['full_run_final_train']['correct_cases'])}
            if armdata['starting_checkpoint_correct']!=int(reference_train.sum()):
                raise ValueError('V22_MISMATCHED_INITIAL_TRAIN_COMPETENCE')
            progress.append(armdata)
            for state,snapshot in (('selected',best),('terminal',terminal)):
                result,dc,dg=_snapshot_metrics(torch,trained,snapshot,spec,mu,sd,
                    args.device,initial_atlases[arm],arm,seed,state,reference_train,groups)
                result.update({'accepted_updates':stats['accepted_updates'],
                    'projection_rescues':stats['projection_rescues'],
                    'updates_rejected':stats['rejected_updates']})
                comparison.append(result);case_changes.extend(dc);group_changes.extend(dg)
                saved[(seed,arm,state)]=snapshot
            for attempt in attempts:events.append({'seed':seed,'arm':arm,**attempt})
            for h in history:progress.append({'seed':seed,'arm':arm,'history':True,**h})
            print('V22_ARM '+probe.canonical({'seed':seed,'arm':arm,
                'accepted':stats['accepted_updates'],'selected_step':best['step'],
                'selected_dev_correct':next(r['dev_correct'] for r in comparison
                      if r['seed']==seed and r['arm']==arm and r['state']=='selected'),
                'selected_dev_six':next(r['dev_complete_six'] for r in comparison
                      if r['seed']==seed and r['arm']==arm and r['state']=='selected'),
                'terminal_train_correct':stats['full_run_final_train']['correct_cases']}),flush=True)
        pairwise.extend(_matched_pair(comparison,seed,'selected'))
        pairwise.extend(_matched_pair(comparison,seed,'terminal'))
    by_seed_arm={(r['seed'],r['arm']):r for r in progress if 'accepted_updates' in r}
    for row in pairwise:
        row['delta_updates_accepted']=(by_seed_arm[(row['seed'],row['arm'])]['accepted_updates']-
                      by_seed_arm[(row['seed'],ARMS[0])]['accepted_updates'])
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'algorithm':'MATCHED_V11_EXPANDED_ACQUISITION_FROM_THREE_VERIFIED_MARGIN_STATES',
        'comparison':comparison,'pairwise':pairwise,'starting_checkpoints':audit,
        'acquisition_progress':[r for r in progress if 'accepted_updates' in r],
        'config':{'seeds':args.seeds,'steps':args.steps,'eval_every':args.eval_every,
             'lr':args.lr,'replay_weight':args.replay_weight,
             'max_halvings':args.max_halvings,'projection_passes':args.projection_passes,
             'max_constraints':args.max_constraints,'margin_width':width,
             'capacity_width':capacity},
        'v14_verified':verified14,'v11_verified':proof,**provenance,
        'training_capture_sha256':capture,'v6_prior_sha256':v19._sha(args.prior),
        'frozen_lineage':data['probe_info']['lineage'],
        'hard_full_train_retention':'UNCHANGED_V10_EXACT_INFERENCE_MONOTONIC_TRAIN_SIGNS',
        'original_task_bypass':'UNCHANGED_V6',
        'same_architecture_optimizer_and_seed_schedule':True,
        'synthetic_train_only_fitting':True,
        'synthetic_dev_used_for_training':False,
        'source_dev_used_for_training':False,
        'previously_seen_test_used_for_training_or_selection':False,
        'production_weights_modified':False,'promotion_authorized':False,
        'capture_bound_router_only':True,'general_production_router':'NOT_CERTIFIED'}
    evidence_files=[Path(args.v14_run)/'post_consolidation_candidate.pt',
                    Path(args.v19_run)/'precision_joint_disposable_candidate.pt',
                    Path(args.v21_run)/'shadow_precision_disposable_candidate.pt']
    evidence_hashes=[v19._sha(x) for x in evidence_files]
    joint._verify_after(data)
    if [v19._sha(x) for x in evidence_files]!=evidence_hashes:
        raise ValueError('V22_STARTING_CHECKPOINTS_MUTATED_DURING_TRAINING')
    out.mkdir(parents=True,exist_ok=False)
    v10._csv(out/'comparison.csv',comparison)
    v10._csv(out/'paired_effects.csv',pairwise)
    v10._csv(out/'starting_checkpoints.csv',audit)
    v10._csv(out/'acquisition_progress.csv',[r for r in progress if 'accepted_updates' in r])
    v10._csv(out/'acquisition_attempts.csv',events)
    v10._csv(out/'dev_case_transitions.csv',case_changes)
    v10._csv(out/'dev_group_transitions.csv',group_changes)
    for seed in args.seeds:
        for arm in ARMS:
            for state in ('selected','terminal'):
                snap=saved[(seed,arm,state)]
                checkpoint={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
                    'seed':seed,'arm':arm,'state':state,'step':snap['step'],
                    'weights':snap['weights'],'mean':torch.as_tensor(mu),
                    'std':torch.as_tensor(sd),'margin_width':width,
                    'capacity_width':capacity,'train_capture_sha256':capture,
                    'starting_checkpoint_sha256':next(r['checkpoint_sha256']
                       for r in audit if r['arm']==arm),
                    'train_ledger_mask':torch.as_tensor(snap['ledger_mask']),
                    'train_acquired_step':torch.as_tensor(snap['acquired_step']),
                    'protected_original_passthrough':True,'promotion_authorized':False}
                dest=out/f'seed-{seed}_{arm}_{state}_candidate.pt'
                torch.save(checkpoint,dest)
                check=torch.load(dest,map_location='cpu',weights_only=True)
                if (check['arm']!=arm or check['state']!=state or
                    not torch.equal(check['train_ledger_mask'],checkpoint['train_ledger_mask'])):
                    raise ValueError('V22_SAVED_CHECKPOINT_RELOAD_MISMATCH')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
         allow_nan=False)+'\n',encoding='utf-8')
    print('V22_COMPLETE '+probe.canonical({'status':report['status'],
        'seeds':args.seeds,'arms':len(ARMS),'snapshots_saved':len(saved),
        'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v14-run','v19-run','v20-run','v21-run','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--seeds',type=_seeds,default=[49,53])
    p.add_argument('--steps',type=int,default=90)
    p.add_argument('--eval-every',type=int,default=15)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--projection-passes',type=int,default=4)
    p.add_argument('--max-constraints',type=int,default=24)
    return p


def main(argv=None):
    run(parser().parse_args(argv));return 0


if __name__=='__main__':
    raise SystemExit(main())
