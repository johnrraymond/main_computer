#!/usr/bin/env python3
"""V23: read-only operand, representation, and specialist-decoder causality audit.

Uses only fingerprint-verified, previously captured Qwen/LoRA activations and
verified V14/V19/V21 and V22 terminal checkpoints. Never trains or changes any
of these models. TRAIN-only ridge readouts are *diagnostic probes*, never
checkpoint updates. No previously seen TEST cases are examined.

A successful readout establishes linear accessibility, not that Qwen performs
subtraction. Feature replacement is an out-of-distribution causal intervention,
not a deployable inference route. A negative readout cannot prove information
absence, especially under the deliberate TRAIN/DEV operand-range shift.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path

from tools import nanojev_gamer_v1_mathematical_acquisition_v22 as v22
from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_zero_sign_frob as oracle
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19

SCHEMA='nanojev.gamer.v1.operand-representation-causality.v23'
DEFAULT_FEATURES=('embedding_last','layer_00_last','layer_09_last',
                  'layer_18_last','layer_27_last','final_last','final_mean100')
SIGN_CLASSES=(-1,0,1)


def _require(condition,reason):
    if not condition:raise ValueError('V23_'+reason)


def _operand_rows(info,split):
    """Parse operands from witnessed prompt *text*, not from answer metadata."""
    out=[]
    rows=info['dataset']
    # This also checks independently regenerated prompt fingerprints and the
    # 612-case capture-bound admission whitelist.
    routes,_=v6._verified_prompt_routes(info)
    count=Counter()
    split_counts=Counter(r['split'] for r in rows)
    ood=sum(r['family']=='hawk_indifference' for r in rows)
    deck=probe.make_deck(split_counts['train']//6,split_counts['dev']//6,
                         (split_counts['test']-ood)//6,ood>0)
    _require(len(deck)==len(rows),'BAD_REGENERATED_DECK_LENGTH')
    for i,(row,case) in enumerate(zip(rows,deck)):
        if row['split']!=split:continue
        _require(row['family']=='signed_offset' and routes[i] in (0,1),
                 'UNSUPPORTED_DIAGNOSTIC_FAMILY')
        if row['surface']=='source_native':
            m=oracle.SOURCE_PATTERN.search(case.prompt)
            _require(m is not None,'NATIVE_OPERANDS_NOT_PRESENT')
            first,second=Fraction(m.group(1)),Fraction(m.group(2))
            aux=None
        elif row['surface']=='spine_difference':
            m=oracle.MATRIX_PATTERN.search(case.prompt)
            _require(m is not None and 'Compute T-R' in case.prompt,
                     'GAME_OPERANDS_NOT_PRESENT')
            first,second=Fraction(m.group(3)),Fraction(m.group(1))
            aux=(Fraction(m.group(2)),Fraction(m.group(4)))
        else:
            raise ValueError('V23_UNKNOWN_PROMPT_SURFACE')
        _require(first-second==Fraction(row['target'])==case.target,
                 'PROMPT_OPERANDS_DO_NOT_REPRODUCE_EXACT_ORACLE')
        _require(row['prompt_sha256']==probe.sha_text(case.prompt) and row==case.public(),
                 'OPERAND_PROMPT_FINGERPRINT_MISMATCH')
        out.append({'archive_index':i,'id':row['id'],'group':row['group'],
                    'surface':row['surface'],'split':split,
                    'first_operand':str(first),'second_operand':str(second),
                    'control_operands':str(aux) if aux is not None else '',
                    'target_fraction':str(first-second),
                    'target_sign':int((first-second)>0)-int((first-second)<0),
                    'prompt_sha256':row['prompt_sha256']})
        count[row['group']]+=1
    _require(len(out)>0 and len(out)%6==0 and all(n==6 for n in count.values()),
             'INCOMPLETE_SIX_CASE_OPERAND_GROUPS')
    bygroup=defaultdict(list)
    for row in out:bygroup[row['group']].append(row)
    for group,group_rows in bygroup.items():
        bysurface=defaultdict(dict)
        for row in group_rows:
            _require(row['target_sign'] not in bysurface[row['surface']],
                     'DUPLICATE_GROUP_SIGN')
            bysurface[row['surface']][row['target_sign']]=row
        _require(set(bysurface)=={'source_native','spine_difference'},
                 'INCOMPLETE_CROSS_SURFACE_PAIR')
        for sign in SIGN_CLASSES:
            a=bysurface['source_native'].get(sign)
            b=bysurface['spine_difference'].get(sign)
            _require(a is not None and b is not None and
                     (a['first_operand'],a['second_operand'],a['target_fraction'])==
                     (b['first_operand'],b['second_operand'],b['target_fraction']),
                     'CROSS_SURFACE_OPERAND_OR_TARGET_DISAGREEMENT')
    return out


def _scores(rows,pred):
    import numpy as np
    pred=np.asarray(pred,dtype=np.int8)
    true=np.asarray([r['target_sign'] for r in rows],dtype=np.int8)
    _require(len(rows)==len(pred) and set(np.unique(pred)).issubset(SIGN_CLASSES),
             'BAD_PREDICTION_ARRAY')
    groups=defaultdict(list)
    for i,row in enumerate(rows):groups[row['group']].append(i)
    complete=sum(len(ix)==6 and bool(np.all(pred[ix]==true[ix])) for ix in groups.values())
    triples=sum(len([i for i in ix if rows[i]['surface']==surface])==3 and
        all(pred[i]==true[i] for i in ix if rows[i]['surface']==surface)
        for ix in groups.values() for surface in ('source_native','spine_difference'))
    return {'cases':len(rows),'correct':int(np.sum(pred==true)),
            'balanced_sign_accuracy':float(np.mean([np.mean(pred[true==s]==s)
                              for s in SIGN_CLASSES if np.any(true==s)])),
            'zero_recall':float(np.mean(pred[true==0]==0)) if np.any(true==0) else None,
            'negative_recall':float(np.mean(pred[true==-1]==-1)) if np.any(true==-1) else None,
            'positive_recall':float(np.mean(pred[true==1]==1)) if np.any(true==1) else None,
            'complete_six':int(complete),'complete_triples':int(triples),'groups':len(groups)}


def _readout_fit(train_x,eval_x,train_rows,eval_rows,*,surface='both',alpha=4.,seed=47):
    """Fixed TRAIN-only kernel ridge; DEV is never used for alpha selection."""
    import numpy as np
    _require(surface in ('both','source_native','spine_difference'),
             'READOUT_BAD_TRAIN_SURFACE')
    train=np.asarray([i for i,r in enumerate(train_rows) if
          surface=='both' or r['surface']==surface],dtype=np.int64)
    _require(len(train)>=12 and len(eval_rows)>=6,'INSUFFICIENT_READOUT_ROWS')
    a=np.asarray(train_x[train],dtype=np.float64)
    b=np.asarray(eval_x,dtype=np.float64)
    _require(a.ndim==2 and b.shape[1]==a.shape[1] and
             np.isfinite(a).all() and np.isfinite(b).all(),
             'NONFINITE_READOUT_FEATURES')
    mu=a.mean(axis=0);sd=np.maximum(a.std(axis=0),1e-3)
    a=np.clip((a-mu)/sd,-16,16);b=np.clip((b-mu)/sd,-16,16)
    scale=a.shape[1]
    k=a@a.T/scale;cross=b@a.T/scale
    signs=np.asarray([train_rows[i]['target_sign'] for i in train],dtype=np.int8)
    y=np.stack([(signs==s).astype(np.float64) for s in SIGN_CLASSES],axis=1)
    operand=np.asarray([[float(Fraction(train_rows[i]['first_operand'])),
                        float(Fraction(train_rows[i]['second_operand']))]
                        for i in train],dtype=np.float64)
    omu=operand.mean(axis=0);osd=np.maximum(operand.std(axis=0),1e-6)
    outcomes=[y,(operand-omu)/osd]
    rng=np.random.default_rng(seed)
    for _ in range(3):
        shuffled=signs.copy();rng.shuffle(shuffled)
        outcomes.append(np.stack([(shuffled==s).astype(np.float64)
                                  for s in SIGN_CLASSES],axis=1))
    rhs=np.concatenate(outcomes,axis=1)
    pred=cross@np.linalg.solve(k+alpha*np.eye(len(train)),rhs)
    labels=np.asarray(SIGN_CLASSES,dtype=np.int8)
    predicted=labels[np.argmax(pred[:,:3],axis=1)]
    pred_operands=pred[:,3:5]*osd+omu
    predicted_subtraction=np.sign(pred_operands[:,0]-pred_operands[:,1]).astype(np.int8)
    expected=np.asarray([[float(Fraction(r['first_operand'])),
                          float(Fraction(r['second_operand']))] for r in eval_rows])
    nulls=[_scores(eval_rows,labels[np.argmax(pred[:,5+3*i:8+3*i],axis=1)])
           ['balanced_sign_accuracy'] for i in range(3)]
    metrics=_scores(eval_rows,predicted)
    metrics.update({'train_surface':surface,'ridge_alpha_fixed_train_only':float(alpha),
        'train_cases':int(len(train)),
        'randomized_label_null_balanced_sign_accuracy':nulls,
        'operand_first_mae':float(np.mean(abs(pred_operands[:,0]-expected[:,0]))),
        'operand_second_mae':float(np.mean(abs(pred_operands[:,1]-expected[:,1]))),
        'operand_pair_max_error_median':float(np.median(np.max(abs(pred_operands-expected),axis=1))),
        'predicted_operand_difference_sign_correct':int(np.sum(
           predicted_subtraction==np.asarray([r['target_sign'] for r in eval_rows]))),
        'readout_not_proof_of_operand_extraction':True,
        'dev_operand_range_extrapolation':True})
    per=[]
    for surface_eval in ('source_native','spine_difference'):
        idx=[i for i,r in enumerate(eval_rows) if r['surface']==surface_eval]
        s=_scores([eval_rows[i] for i in idx],predicted[idx])
        per.append({'eval_surface':surface_eval,**s})
    return metrics,per,predicted


def _contrast_geometry(train_x,dev_x,train_rows,dev_rows):
    """Within-group contrast rank, not a deployable predictor (uses group pairing)."""
    import numpy as np
    def grouped(rows):
        d=defaultdict(dict)
        for i,r in enumerate(rows):d[(r['group'],r['surface'])][r['target_sign']]=i
        _require(all(set(x)==set(SIGN_CLASSES) for x in d.values()),
                 'GROUPED_GEOMETRY_INCOMPLETE')
        return d
    tr,dev=grouped(train_rows),grouped(dev_rows)
    a=np.asarray(train_x,dtype=np.float64);b=np.asarray(dev_x,dtype=np.float64)
    mu=a.mean(axis=0);sd=np.maximum(a.std(axis=0),1e-3)
    a=(a-mu)/sd;b=(b-mu)/sd
    normals=[]
    for loc in tr.values():
        d=a[loc[1]]-a[loc[-1]]
        if np.linalg.norm(d)>1e-9:normals.append(d/np.linalg.norm(d))
    axis=np.mean(normals,axis=0) if normals else np.zeros(a.shape[1])
    axis_norm=np.linalg.norm(axis)
    if axis_norm>1e-12:axis/=axis_norm
    contrasts=[float((b[loc[1]]-b[loc[-1]])@axis)
               for loc in dev.values()]
    chosen=[];gap=[]
    for group in sorted({key[0] for key in dev}):
        native=dev[(group,'source_native')]
        game=dev[(group,'spine_difference')]
        left=np.stack([b[native[s]] for s in SIGN_CLASSES])
        right=np.stack([b[game[s]] for s in SIGN_CLASSES])
        distances=((left[:,None,:]-right[None,:,:])**2).mean(axis=2)
        chosen.extend((np.argmin(distances,axis=1)==np.arange(3)).astype(int).tolist())
        chosen.extend((np.argmin(distances,axis=0)==np.arange(3)).astype(int).tolist())
        gap.append(float(np.mean(distances[~np.eye(3,dtype=bool)])-np.mean(np.diag(distances))))
    return {'group_anchored_sign_contrast_positive_fraction':float(np.mean(np.asarray(contrasts)>0)),
            'group_anchored_contrast_cases':len(contrasts),
            'same_answer_cross_surface_nearest_neighbor_fraction':float(np.mean(chosen)),
            'cross_surface_matched_distance_advantage':float(np.mean(gap)),
            'cross_surface_groups':len(gap),
            'group_anchor_is_oracle_scaffolding_not_deployable':True}


def _swap_plan(rows):
    """Counterfactual index plan independent of model predictions or labels."""
    look={(r['group'],r['surface'],r['target_sign']):i for i,r in enumerate(rows)}
    result=[]
    for i,r in enumerate(rows):
        target=r['target_sign'];group=r['group'];surface=r['surface']
        other='spine_difference' if surface=='source_native' else 'source_native'
        result.append({'recipient':i,'donor':look[(group,other,target)],
                       'kind':'same_answer_cross_surface_feature_only'})
        if target in (-1,1):
            result.append({'recipient':i,'donor':look[(group,surface,-target)],
                           'kind':'opposite_sign_same_surface_feature_only'})
            result.append({'recipient':i,'donor':look[(group,surface,-target)],
                           'kind':'opposite_sign_same_surface_baseline_only'})
            result.append({'recipient':i,'donor':look[(group,surface,-target)],
                           'kind':'opposite_sign_same_surface_full_input_control'})
    return result


def _interventions(torch,model,spec,mu,sd,device,rows):
    """Swap recorded feature or frozen-head baseline, never prompt or oracle label."""
    import numpy as np
    atlas_rows,x,old,y,routes=v7._split_arrays(spec,'dev')
    _require([r['id'] for r in rows]==[r['id'] for r in atlas_rows],
             'INFERENCE_AND_ARCHIVE_ORDER_DISAGREE')
    baseline,_=v6._predict(torch,model,x,old,routes,mu,sd,device)
    swaps=_swap_plan(rows)
    xx=np.stack([x[s['donor']] if s['kind'].endswith('feature_only') or
                 s['kind'].endswith('full_input_control') else x[s['recipient']]
                 for s in swaps],axis=0)
    oo=np.asarray([old[s['donor']] if s['kind'].endswith('baseline_only') or
                   s['kind'].endswith('full_input_control') else old[s['recipient']]
                   for s in swaps],dtype=np.float64)
    rr=np.asarray([routes[s['recipient']] for s in swaps],dtype=np.float32)
    changed,_=v6._predict(torch,model,xx,oo,rr,mu,sd,device)
    out=[]
    for s,cls in zip(swaps,changed):
        i=s['recipient'];j=s['donor'];kind=s['kind']
        if kind.endswith('full_input_control'):
            _require(int(cls)==int(baseline[j]),'FULL_SWAP_POSITIVE_CONTROL_MISMATCH')
        out.append({'recipient_id':rows[i]['id'],'donor_id':rows[j]['id'],
            'surface':rows[i]['surface'],'kind':kind,
            'recipient_sign':rows[i]['target_sign'],'donor_sign':rows[j]['target_sign'],
            'before_predicted_sign':int(baseline[i]),
            'after_predicted_sign':int(cls),
            'changed_prediction':int(cls!=baseline[i]),
            'rescued_recipient':int(baseline[i]!=rows[i]['target_sign'] and
                                    cls==rows[i]['target_sign']),
            'harmed_recipient':int(baseline[i]==rows[i]['target_sign'] and
                                   cls!=rows[i]['target_sign']),
            'follows_donor_truth':int(cls==rows[j]['target_sign']),
            'recipient_correct_before':int(baseline[i]==rows[i]['target_sign'])})
    summaries=[]
    for kind in sorted(set(s['kind'] for s in swaps)):
        selected=[r for r in out if r['kind']==kind]
        summaries.append({'kind':kind,'probes':len(selected),
             'sign_changes':sum(r['changed_prediction'] for r in selected),
             'rescues':sum(r['rescued_recipient'] for r in selected),
             'harms':sum(r['harmed_recipient'] for r in selected),
             'follows_donor_truth':sum(r['follows_donor_truth'] for r in selected),
             'out_of_distribution_counterfactual_not_proof_of_operand_role':True})
    return out,summaries


def _verify_v22(torch,args,data,proof,verified14,capture,provenance,starts,model,
                 spec,mu,sd,groups):
    """Require exact V22 historical report and actual checkpoint inference."""
    import numpy as np
    directory=Path(args.v22_run);p=directory/'report.json'
    _require(p.is_file(),'V22_REPORT_MISSING')
    report=json.loads(p.read_text(encoding='utf-8'))
    checks={'schema':report.get('schema')==v22.SCHEMA,
            'status':report.get('status')=='EXPERIMENT_COMPLETE_NO_PROMOTION',
            'capture':report.get('training_capture_sha256')==capture,
            'v14':report.get('v14_verified')==verified14,
            'v11':report.get('v11_verified')==proof,
            'lineage':report.get('frozen_lineage')==data['probe_info']['lineage'],
            'router':report.get('capture_bound_router_only') is True,
            'no_production_write':report.get('production_weights_modified') is False,
            'no_promotion':report.get('promotion_authorized') is False,
            'no_test_training':report.get('previously_seen_test_used_for_training_or_selection') is False,
            'no_dev_training':report.get('synthetic_dev_used_for_training') is False,
            'v19_hash':report.get('v19_report_sha256')==provenance['v19_report_sha256'],
            'v20_hash':report.get('v20_report_sha256')==provenance['v20_report_sha256'],
            'v21_hash':report.get('v21_report_sha256')==provenance['v21_report_sha256'],
            'v19_checkpoint':report.get('v19_checkpoint_sha256')==provenance['v19_checkpoint_sha256'],
            'v21_checkpoint':report.get('v21_checkpoint_sha256')==provenance['v21_checkpoint_sha256'],
            'start_sha':{r['arm']:r['checkpoint_sha256'] for r in report.get('starting_checkpoints',[])}==
                {r['arm']:r['checkpoint_sha256'] for r in args.start_audit}}
    failed=[k for k,v in checks.items() if not v]
    _require(not failed,'V22_REPORT_PROVENANCE_MISMATCH: '+','.join(failed))
    expected={(int(r['seed']),r['arm'],r['state']):r for r in report.get('comparison',[])}
    seeds=report['config']['seeds']
    _require(set(expected)=={(int(seed),arm,state) for seed in seeds
                 for arm in v22.ARMS for state in ('selected','terminal')},
             'V22_COMPARISON_NOT_COMPLETE')
    terminals={};audit=[]
    freeze_ref={k:v for k,v in starts[v22.ARMS[0]].items() if k.startswith('base.base.')}
    width=report['config']['margin_width'];capacity=report['config']['capacity_width']
    for seed in seeds:
        for arm in v22.ARMS:
            file=directory/f'seed-{seed}_{arm}_terminal_candidate.pt'
            _require(file.is_file(),'V22_TERMINAL_FILE_MISSING: '+file.name)
            cp=torch.load(file,map_location='cpu',weights_only=True)
            ref=expected[(seed,arm,'terminal')]
            good=(cp.get('schema')==v22.SCHEMA and
                  cp.get('status')=='DISPOSABLE_NON_PRODUCTION' and
                  cp.get('promotion_authorized') is False and
                  cp.get('protected_original_passthrough') is True and
                  cp.get('seed')==seed and cp.get('arm')==arm and
                  cp.get('state')=='terminal' and cp.get('step')==ref['step'] and
                  cp.get('train_capture_sha256')==capture and
                  cp.get('starting_checkpoint_sha256')==next(r['checkpoint_sha256']
                         for r in args.start_audit if r['arm']==arm) and
                  cp.get('margin_width')==width and cp.get('capacity_width')==capacity and
                  np.array_equal(cp['mean'].numpy(),mu) and
                  np.array_equal(cp['std'].numpy(),sd) and
                  cp['weights'].keys()==starts[arm].keys() and
                  all(torch.equal(cp['weights'][k],v) for k,v in freeze_ref.items()))
            _require(good,'V22_TERMINAL_CHECKPOINT_PROVENANCE: '+file.name)
            model.load_state_dict(cp['weights'],strict=True);model.eval()
            seen=v9._correct_train(torch,model,spec,mu,sd,args.device)
            ledger=cp['train_ledger_mask'].cpu().numpy().astype(bool)
            acquired=cp['train_acquired_step'].cpu().numpy()
            _require(np.array_equal(seen,ledger) and np.array_equal(seen,acquired>=0) and
                     int(seen.sum())==ref['train_correct'] and
                     bool(seen[args.initial_correct].all()),
                     'V22_TERMINAL_ACTUAL_TRAIN_RETENTION: '+file.name)
            ret,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
            atlas=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
            metrics=v22._atlas_metrics(atlas)
            _require(ret['status']=='PASS' and metrics['dev_correct']==ref['dev_correct'] and
                     metrics['dev_complete_six']==ref['dev_complete_six'] and
                     metrics['zero_correct']==ref['zero_correct'] and
                     abs(dev['all']['balanced_sign_accuracy']-ref['dev_sign'])<1e-12,
                     'V22_TERMINAL_ACTUAL_INFERENCE_DRIFT: '+file.name)
            name=f'v22_seed{seed}_{arm}_terminal'
            terminals[name]={k:v.detach().cpu().clone() for k,v in cp['weights'].items()}
            audit.append({'name':name,'checkpoint_sha256':v19._sha(file),
                'train_correct':int(seen.sum()),'dev_correct':metrics['dev_correct'],
                'dev_complete_six':metrics['dev_complete_six'],
                'original_retention':'PASS'})
    return terminals,audit,v19._sha(p)


def _run(args):
    import numpy as np
    import torch
    _require(not str(args.device).startswith('cuda') or torch.cuda.is_available(),'CUDA_NOT_AVAILABLE')
    sources=[Path(getattr(args,key)) for key in ('run','expanded_run','input',
       'source_features','prior','v11_run','v14_run','v19_run','v20_run',
       'v21_run','v22_run')]
    out=lab._output(args.output,sources)
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    groups,capture=v9._groups_and_fingerprints(spec)
    _,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    _,cp11,_,proof=v12._verify_v11(torch,args.v11_run,data,capture,
                                  v19._sha(args.prior),spec,args.device)
    _require(np.array_equal(cp11['selected']['mean'].numpy(),mu) and
             np.array_equal(cp11['selected']['std'].numpy(),sd),'NORMALIZER_DRIFT')
    report14,post,model,verified14=v15._check_prior14(args.v14_run,proof,capture,
      v19._sha(args.prior),data,args.focus_group,torch,spec,mu,sd,args.device)
    starts,start_audit,prov=v22._verified_starts(torch,args,data,spec,mu,sd,capture,
                          proof,verified14,report14,post,model)
    args.start_audit=start_audit
    args.initial_correct=v9._correct_train(torch,model,spec,mu,sd,args.device)
    terminals,terminal_audit,v22_sha=_verify_v22(torch,args,data,proof,verified14,
        capture,prov,starts,model,spec,mu,sd,groups)
    historical=[Path(args.v14_run)/'post_consolidation_candidate.pt',
      Path(args.v19_run)/'precision_joint_disposable_candidate.pt',
      Path(args.v21_run)/'shadow_precision_disposable_candidate.pt',
      Path(args.v22_run)/'report.json']
    for seed in json.loads((Path(args.v22_run)/'report.json').read_text())['config']['seeds']:
        for arm in v22.ARMS:
            historical.append(Path(args.v22_run)/f'seed-{seed}_{arm}_terminal_candidate.pt')
    historical_hashes={str(p):v19._sha(p) for p in historical}
    starts.update(terminals)
    parsed_train=_operand_rows(data['probe_info'],'train')
    parsed_dev=_operand_rows(data['probe_info'],'dev')
    train_index=np.asarray([r['archive_index'] for r in parsed_train],dtype=int)
    dev_index=np.asarray([r['archive_index'] for r in parsed_dev],dtype=int)
    _require(len(parsed_train)==len(spec['train_synthetic'][3]) and
             len(parsed_dev)==len(v7._split_arrays(spec,'dev')[0]) and
             [r['id'] for r in parsed_dev]==[r['id'] for r in v7._split_arrays(spec,'dev')[0]],
             'OPERAND_MODEL_CAPTURE_ALIGNMENT_FAILED')
    arrays=data['probe_arrays']
    features=[key for key in DEFAULT_FEATURES if all(
        variant+'__'+key in arrays for variant in ('base','lora'))]
    _require(len(features)>=3,'REQUIRED_INTERMEDIATE_CAPTURES_MISSING')
    readouts=[];geometry=[];readout_cases=[];readout_lookup={}
    for variant in ('base','lora'):
        for feature in features:
            X=np.asarray(arrays[variant+'__'+feature],dtype=np.float32)
            train_X=X[train_index];dev_X=X[dev_index]
            geo=_contrast_geometry(train_X,dev_X,parsed_train,parsed_dev)
            geometry.append({'variant':variant,'feature':feature,**geo})
            for training in ('both','source_native','spine_difference'):
                summary,per,predictions=_readout_fit(train_X,dev_X,parsed_train,parsed_dev,
                                     surface=training,alpha=args.ridge_alpha)
                readouts.append({'variant':variant,'feature':feature,
                                 **summary,'by_eval_surface':per})
                readout_lookup[(variant,feature,training)]=predictions
                for case,sign in zip(parsed_dev,predictions):
                    readout_cases.append({'variant':variant,'feature':feature,
                        'train_surface':training,'id':case['id'],
                        'surface':case['surface'],'target_sign':case['target_sign'],
                        'readout_sign':int(sign),'correct':int(sign==case['target_sign'])})
            print('V23_READOUT '+probe.canonical({'variant':variant,'feature':feature,
                  'groups':geo['cross_surface_groups']}),flush=True)
    scores=[];swaps=[];swap_summary=[];case_localization=[]
    for name,state in starts.items():
        model.load_state_dict(state,strict=True);model.eval()
        ret,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
        atlas=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
        actual=v22._atlas_metrics(atlas)
        _require(ret['status']=='PASS','MODEL_ORIGINAL_RETENTION_FAILED: '+name)
        scores.append({'model':name,'dev_sign':dev['all']['balanced_sign_accuracy'],
                       'train_source':name in v22.ARMS,**actual})
        for i,case in enumerate(atlas):
            detail_row={'model':name,'id':case['id'],'surface':case['surface'],
              'target_sign':case['target_sign'],
              'specialist_sign':case['predicted_sign'],
              'specialist_correct':case['correct'],
              'baseline_sign':case['baseline_sign']}
            for variant in ('base','lora'):
                key=(variant,'final_last','both')
                if key in readout_lookup:
                    sign=int(readout_lookup[key][i])
                    detail_row[variant+'_final_readout_sign']=sign
                    detail_row[variant+'_final_readout_correct']=int(sign==case['target_sign'])
            if 'lora_final_readout_correct' in detail_row:
                detail_row['probe_correct_specialist_wrong']=int(
                  detail_row['lora_final_readout_correct'] and not case['correct'])
            case_localization.append(detail_row)
        detail,summary=_interventions(torch,model,spec,mu,sd,args.device,parsed_dev)
        swaps.extend({'model':name,**r} for r in detail)
        swap_summary.extend({'model':name,**r} for r in summary)
        print('V23_DECODER '+probe.canonical({'model':name,
              'dev_correct':actual['dev_correct'],
              'feature_swaps':sum(r['sign_changes'] for r in summary if 'feature_only' in r['kind'])}),flush=True)
    model.load_state_dict(starts[v22.ARMS[0]],strict=True);model.eval()
    joint._verify_after(data)
    # Historical evidence is immutable throughout the read-only diagnostic.
    # In addition to the original source/capture _verify_after check, audit
    # later disposable checkpoint fingerprints on both sides of all probes.
    _require(all(v19._sha(p)==historical_hashes[str(p)] for p in historical),
             'HISTORICAL_CHECKPOINT_MUTATED_DURING_AUDIT')
    report={'schema':SCHEMA,'status':'READ_ONLY_VERIFIED_NO_PROMOTION',
       'algorithm':'GROUP_MATCHED_OPERAND_READOUT_AND_DECODER_FEATURE_SWAPS',
       'frozen_lineage':data['probe_info']['lineage'],
       'v22_report_sha256':v22_sha,'verified_v14':verified14,
       'v19_report_sha256':prov['v19_report_sha256'],
       'v20_report_sha256':prov['v20_report_sha256'],
       'v21_report_sha256':prov['v21_report_sha256'],
       'v22_terminal_checkpoints_verified':terminal_audit,
       'starting_checkpoints_verified':start_audit,
       'train_operand_groups':len(parsed_train)//6,
       'dev_operand_groups':len(parsed_dev)//6,
       'readout_probes':readouts,'group_geometry':geometry,
       'decoder_scores':scores,'intervention_summary':swap_summary,
       'case_localization_summary':[{'model':m,
          'probe_correct_specialist_wrong':sum(x.get('probe_correct_specialist_wrong',0)
                       for x in case_localization if x['model']==m)} for m in starts],
       'history_checkpoints_unchanged':True,
       'original_task_bypass':'UNCHANGED_V6','no_trainable_parameters_modified':True,
       'no_lora_or_qwen_forward_or_training':True,
       'readout_fit_synthetic_train_only':True,
       'dev_used_for_readout_fitting_or_hyperparameter_selection':False,
       'previously_seen_test_used_for_fitting_selection_or_metrics':False,
       'historical_capture_metadata_includes_previously_seen_test':True,
       'production_weights_modified':False,'checkpoint_saved':False,
       'promotion_authorized':False,
       'limits':[
          'A linear readout can access correlations without proving correct model arithmetic',
          'DEV operands are deliberately outside the TRAIN operand range: poor extrapolation is inconclusive',
          'Matched-group geometry uses diagnostic oracle grouping and is not a deployable router',
          'Feature swaps are out-of-distribution and show decoder dependence, not a unique causal origin',
          'An arithmetic parser uses prompt text with an independent exact oracle and is not a Qwen prediction',
          'No global or full-sequence operand causality is established from cached summary activations']}
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'parsed_operands_train.csv',parsed_train)
    _csv(out/'parsed_operands_dev.csv',parsed_dev)
    _csv(out/'readout_probes.csv',[{k:v for k,v in row.items() if k!='by_eval_surface'}
                                  for row in readouts])
    _csv(out/'group_geometry.csv',geometry)
    _csv(out/'decoder_scores.csv',scores)
    _csv(out/'readout_case_predictions.csv',readout_cases)
    _csv(out/'case_localization.csv',case_localization)
    _csv(out/'interventions.csv',swaps)
    _csv(out/'intervention_summary.csv',swap_summary)
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                 allow_nan=False)+'\n',encoding='utf-8')
    print('V23_COMPLETE '+probe.canonical({'status':report['status'],
        'readouts':len(readouts),'models':len(scores),
        'interventions':len(swaps),'checkpoint_saved':False,
        'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('audit',),default='audit')
    for name in ('run','expanded-run','input','source-features','prior',
                 'v11-run','v14-run','v19-run','v20-run','v21-run','v22-run','output'):
        p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--focus-group',default=v12.FOCUS)
    p.add_argument('--ridge-alpha',type=float,default=4.)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    _require(args.ridge_alpha>0 and args.ridge_alpha<1e4,'INVALID_RIDGE_ALPHA')
    _run(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
