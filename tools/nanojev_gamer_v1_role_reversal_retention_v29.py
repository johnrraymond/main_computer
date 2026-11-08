#!/usr/bin/env python3
"""V29: role-reversal selector-only acquisition starting at the V28 hybrid.

The V28 hybrid checkpoint is a *required* starting point. Every previously
correct V28 TRAIN selection, including all augmented TRAIN acquisitions, is a
hard retention constraint on every accepted update. V28 neural-only is an
immutable control. No DEV or TEST observations enter fitting or checkpoint
selection. Exact arithmetic is a diagnostic calculator, not learned inference.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from fractions import Fraction
import gc
import json
from pathlib import Path
import numpy as np
from tools import nanojev_gamer_v1_selector_retention_training_v28 as v28
from tools import nanojev_gamer_v1_candidate_span_causality_v27 as v27
from tools import nanojev_gamer_v1_operand_span_causality_v26 as v26
from tools import nanojev_gamer_v1_operand_token_causality_v24 as v24
from tools import nanojev_gamer_v1_sign_information_probe as probe
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_two_game_spine_smoke as spine

SCHEMA='nanojev.gamer.v1.role-reversal-retention-training.v29'
KINDS=('reordered_prefix_numeric','reordered_suffix_numeric',
       'reordered_false_history','reordered_instruction')


def _require(ok, reason):
    if not ok:raise ValueError('V29_'+reason)


def _challenge_entries(original, indices, *, heldout=False):
    """Generate TRAIN-only reversal exercises; oracle spans are for labels only."""
    out=[]
    for kind in KINDS:
        for i in indices:
            src=original[i]
            _require(src['scope']=='train','TRAIN_SOURCE_LEAK')
            base=v27._challenge_prompt(src['prompt'],src['surface'],'role_reordered')
            a,b=v27._truth_spans(base,src['surface'])
            if kind=='reordered_prefix_numeric':
                prefix='Archive-only references 101317 and 271829; they are NOT task operands. '
                suffix=''
            elif kind=='reordered_suffix_numeric':
                prefix='';suffix=' Archive-only references 101317 and 271829 are irrelevant.'
            elif kind=='reordered_false_history':
                if src['surface']=='spine_difference':
                    prefix=('Historical example, not the current question: '
                            'CC=11, CD=12, DC=13, DD=14. Current question: ')
                else:
                    prefix=('Historical example, not the current question: '
                            'a spacecraft is at signed coordinate 11, '
                            'while a navigation beacon is at signed coordinate 14. '
                            'Current question: ')
                suffix=''
            elif kind=='reordered_instruction':
                prefix=('For this trial, operand roles are determined by the current '
                        'named quantities, not by the order in which numbers appear. '
                        'Ignore unrelated numeric reference 101317. ')
                suffix=' Ignore any earlier reference values.'
            else:raise ValueError('V29_UNKNOWN_PERTURBATION')
            prompt=prefix+base+suffix
            shifts=(len(prefix),len(prefix))
            spans=((a[0]+shifts[0],a[1]+shifts[0]),
                   (b[0]+shifts[1],b[1]+shifts[1]))
            truth=Fraction(prompt[slice(*spans[0])])-Fraction(prompt[slice(*spans[1])])
            _require(truth==src['target'] and spans[0]!=spans[1],
                     'ROLE_REORDER_TARGET_OR_SPAN_MISMATCH')
            candidates=[tuple(c['span']) for c in v27._numeric_candidates(prompt)]
            _require(all(span in candidates for span in spans),
                     'ROLE_REORDER_SPANS_NOT_CANDIDATES')
            out.append({'row_index':src['row_index'],
                'id':src['id']+'::'+('heldout_' if heldout else 'train_')+kind,
                'scope':'train_role_holdout' if heldout else 'train_role_aug',
                'kind':kind,'prompt':prompt,'surface':src['surface'],
                'target':truth,'_oracle':spans})
    return out


def _verify_augmented_archive(v28_run,report,original,fit):
    """Read immutable V28 augmented source and reconstruct its TRAIN oracle."""
    augmented=v28._train_entries(original,fit)
    a=Path(v28_run)/'augmented_train_layer18.npz'
    m=Path(v28_run)/'augmented_train_candidates.json'
    _require(a.is_file() and m.is_file() and
             v19._sha(a)==report['augmented_archive_sha256'],
             'V28_AUGMENTED_ARCHIVE_HASH')
    metadata=json.loads(m.read_text(encoding='utf-8'))
    with np.load(a,allow_pickle=False) as z:
        _require('layer_18' in z and 'token_ids' in z,'V28_AUGMENTED_FIELDS')
        vectors=np.asarray(z['layer_18'],dtype=np.float32).copy()
        tokens=np.asarray(z['token_ids'],dtype=np.int64)
    _require(len(augmented)==report['augmented_train_cases'] and
             len(vectors)==len(tokens) and np.isfinite(vectors).all(),
             'V28_AUGMENTED_COUNTS')
    cursor=0;counts=defaultdict(int)
    for c in metadata:
        index=int(c['case_index']);begin=int(c['start']);end=int(c['stop'])
        _require(0<=index<len(augmented) and begin==cursor and end>begin and
                 end<=len(tokens) and np.array_equal(tokens[begin:end],c['token_ids']) and
                 tuple(c['span']) in [tuple(x['span']) for x in
                    v27._numeric_candidates(augmented[index]['prompt'])],
                 'V28_AUGMENTED_METADATA')
        counts[index]+=1;cursor=end
    _require(cursor==len(tokens) and all(counts[i]==len(v27._numeric_candidates(
             augmented[i]['prompt'])) for i in range(len(augmented))),
             'V28_AUGMENTED_METADATA_COVERAGE')
    by_case,gold=v27._gold_candidate_ids(augmented,metadata)
    return augmented,metadata,vectors,by_case,gold


def _load_v28(args,info,base_weights,original,fit,original_vectors):
    import torch
    root=Path(args.v28_run);rp=root/'report.json';cp=root/'disposable_selector_candidates.pt'
    _require(rp.is_file() and cp.is_file(),'V28_REPORT_OR_CHECKPOINT_MISSING')
    report=json.loads(rp.read_text(encoding='utf-8'))
    _require(report.get('schema')==v28.SCHEMA and
             report.get('status')=='SELECTOR_TRAINING_COMPLETE_NO_PROMOTION' and
             report.get('frozen_lineage')==info['lineage'] and
             report.get('source_dataset_sha256')==info['dataset_sha256'] and
             report.get('promotion_authorized') is False and
             report.get('production_weights_modified') is False and
             report.get('test_used_for_fitting') is False and
             report.get('dev_used_for_checkpoint_selection') is False and
             report.get('original_task_bypass')=='UNCHANGED_V6' and
             all(report.get('acceptance',{}).get(p,{}).get('all_gates_met') is True
                 for p in ('hybrid','neural_only')) and
             report.get('v27_report_sha256')==v19._sha(Path(args.v27_run)/'report.json') and
             report.get('disposable_selector_checkpoint_sha256')==v19._sha(cp),
             'V28_PROVENANCE_MISMATCH')
    checkpoint=torch.load(cp,map_location='cpu',weights_only=True)
    _require(checkpoint.get('schema')==v28.SCHEMA and
             checkpoint.get('production') is False and
             checkpoint.get('base_stage')==v28.STAGE and
             checkpoint.get('training_only_checkpoint_selection') is True and
             'candidate_delta' in checkpoint and
             set(checkpoint['candidate_delta'])=={'hybrid','neural_only'},
             'V28_CHECKPOINT_STRUCTURE')
    stored=np.asarray(checkpoint['base_weight'].numpy(),dtype=np.float64)
    _require(stored.shape==base_weights.shape and np.allclose(stored,base_weights,
             atol=1e-10,rtol=1e-10),'V28_BASE_WEIGHT_RECOMPUTE_MISMATCH')
    deltas={}
    for p in ('hybrid','neural_only'):
        theta=checkpoint['candidate_delta'][p].detach().cpu().numpy().astype(np.float32)
        _require(theta.ndim==2 and theta.shape[0]==2 and
                 theta.shape[1]==stored.shape[1]+(len(v28.MARKERS)+13 if p=='hybrid' else 0)
                 and np.isfinite(theta).all(),'V28_DELTA_SHAPE')
        deltas[p]=theta
    augmented,meta,vec,by,gold=_verify_augmented_archive(args.v28_run,report,original,fit)
    aug_case={p:v28._case_features(augmented,meta,vec,by,gold,
        list(range(len(augmented))),base_weights,use_context=p=='hybrid')
        for p in deltas}
    for p in deltas:
        _require(v28._gain(aug_case[p],deltas[p])==report['training'][p]['final_aug_correct'],
                 'V28_TRAIN_REPLAY_MISMATCH_'+p.upper())
    return report,deltas,augmented,meta,vec,by,gold,aug_case


def _train_from_v28(torch,protected,role_old,new_role,anchor,*,steps,lr,
                    batch_size,eval_every,max_halvings,seed):
    """TRAIN-only constrained continuation; all V28-correct TRAIN are protected."""
    _require(steps>0 and eval_every>0 and lr>0 and batch_size>=8 and
             max_halvings>=0,'BAD_TRAIN_CONFIG')
    baseline=np.asarray([v28._predict(c,anchor)==c['truth'] for c in protected],bool)
    _require(np.any(baseline),'NO_PROTECTED_COMPETENCE')
    old_mask=np.asarray([v28._predict(c,anchor)==c['truth'] for c in role_old],bool)
    old_initial=int(old_mask.sum());new_initial=v28._gain(new_role,anchor)
    best=anchor.copy();best_old=old_initial;best_new=new_initial
    theta=torch.nn.Parameter(torch.from_numpy(anchor.copy()))
    optimizer=torch.optim.AdamW([theta],lr=lr,weight_decay=0)
    fixed=torch.from_numpy(anchor.copy())
    rng=np.random.default_rng(seed)
    history=[];accepted=0;rejected=0
    for step in range(1,steps+1):
        # Prioritize the acquired role-reversal misses and extra TRAIN
        # reversals, while replaying acquired original + augmented cases.
        old_indices=np.flatnonzero(~old_mask)
        if not len(old_indices):old_indices=np.arange(len(role_old))
        n_replay=max(2,batch_size//4)
        n_old=max(2,batch_size//3)
        n_new=batch_size-n_replay-n_old
        sample=[protected[int(i)] for i in rng.choice(len(protected),n_replay,replace=True)]
        sample +=[role_old[int(i)] for i in rng.choice(old_indices,n_old,replace=True)]
        sample +=[new_role[int(i)] for i in rng.choice(len(new_role),n_new,replace=True)]
        optimizer.zero_grad(set_to_none=True)
        loss=v28._minibatch_loss(torch,sample,theta,list(range(len(sample))))
        loss=loss+0.002*torch.mean((theta-fixed)**2)
        loss.backward();torch.nn.utils.clip_grad_norm_([theta],2.0)
        optimizer.step()
        if step%eval_every and step!=steps:continue
        proposal=theta.detach().cpu().numpy().copy()
        accepted_candidate=None;used_halves=None;new_old=None;new_new=None
        for half in range(max_halvings+1):
            trial=best+(proposal-best)*(0.5**half)
            if v28._retention(protected,trial,baseline):continue
            old_now=v28._gain(role_old,trial)
            new_now=v28._gain(new_role,trial)
            # Old acquired reversals cannot regress (also included in protection).
            if old_now<best_old or old_now*4+new_now<=best_old*4+best_new:continue
            accepted_candidate=trial;used_halves=half
            new_old=old_now;new_new=new_now;break
        if accepted_candidate is None:rejected+=1
        else:
            best=accepted_candidate.copy();best_old=new_old;best_new=new_new
            accepted+=1
        entry={'step':step,'loss':float(loss.item()),
            'accepted':accepted_candidate is not None,'halvings':used_halves,
            'protected_correct_from_v28':int(baseline.sum()),
            'protected_losses':v28._retention(protected,best,baseline),
            'old_reordered_correct':int(best_old),'old_reordered_total':len(role_old),
            'new_role_correct':int(best_new),'new_role_total':len(new_role)}
        history.append(entry)
        print('V29_TRAIN '+probe.canonical(entry),flush=True)
    _require(v28._retention(protected,best,baseline)==0,'RETENTION_LOST')
    return best,history,{'accepted':accepted,'rejected':rejected,
        'v28_correct_protected':int(baseline.sum()),'v28_original_role_correct':old_initial,
        'final_original_role_correct':int(best_old),
        'new_role_initial':new_initial,'new_role_final':int(best_new)}


def _score(entries,metadata,vectors,by_case,gold,indices,base_weights,
           weights,tok_fit,tok,scope,policy):
    return v28._full_evaluation(entries,metadata,vectors,by_case,gold,indices,
             base_weights,weights,tok_fit,tok,scope=scope,policy=policy)


def _run(args):
    import torch
    from tools import nanojev_gamer_v1_handoff as gamer
    _require(args.mode=='train' and args.steps>0 and args.eval_every>0 and
             args.batch_size>=8 and args.lr>0 and args.max_halvings>=0 and
             args.capture_batch in range(1,9) and args.max_candidates>=6 and
             1<=args.max_candidate_tokens<=64 and args.max_input_tokens>0 and
             (not str(args.device).startswith('cuda') or torch.cuda.is_available()),
             'BAD_CONFIG_OR_DEVICE')
    src=[Path(getattr(args,k)) for k in ('run','expanded_run','input',
         'source_features','prior','v11_run','v14_run','v19_run','v20_run',
         'v21_run','v22_run','v23_run','v24_run','v25_run','v26_run','v27_run','v28_run')]
    out=lab._output(args.output,src)
    _require(not out.exists(),'OUTPUT_ALREADY_EXISTS')
    _,train,dev,rows,prompts,_,info=v26._verify_source(args)
    report27,original,meta,vec,by_case,gold=v28._load_v27(args,rows,prompts,train,info)
    fit,hold,heldout_groups=v24._group_fit_split(train)
    _require(len(fit)>0 and len(hold)>0 and len(dev)>0 and
             args.max_input_tokens<=info['lineage']['configuration']['max_length'],
             'INVALID_DATASET_OR_INPUT_LENGTH')
    base_weight=np.asarray(v27._fit_neural_selector({v28.STAGE:vec},meta,original,
                                  gold,fit,v28.STAGE),dtype=np.float64)
    report28,deltas,old_aug,old_meta,old_vec,old_by,old_gold,old_features=\
        _load_v28(args,info,base_weight,original,fit,vec)
    original_cases={p:v28._case_features(original,meta,vec,by_case,gold,
                list(range(len(train))),base_weight,use_context=p=='hybrid')
                    for p in ('hybrid','neural_only')}
    # Preserve EVERY prior correct selection, not merely the 288 original ones.
    protected=original_cases['hybrid']+old_features['hybrid']
    v28_anchor=deltas['hybrid']
    protected_mask=np.asarray([v28._predict(c,v28_anchor)==c['truth'] for c in protected],bool)
    _require(protected_mask[:len(train)].sum()==len(train) and
             protected_mask[len(train):].sum()==report28['training']['hybrid']['final_aug_correct'],
             'V28_ACQUIRED_TRAIN_PARITY')
    old_role=[c for i,c in enumerate(old_features['hybrid'])
              if old_aug[i]['kind']=='role_reordered']
    new_train=_challenge_entries(original,fit)
    heldout=_challenge_entries(original,hold,heldout=True)
    watched=[Path(info['lineage']['checkpoint_path']),
        Path(args.v27_run)/'report.json',Path(args.v27_run)/'base_all_candidate_activations.npz',
        Path(args.v28_run)/'report.json',Path(args.v28_run)/'disposable_selector_candidates.pt',
        Path(args.v28_run)/'augmented_train_layer18.npz',
        Path(args.v28_run)/'augmented_train_candidates.json']
    before={str(p):v19._sha(p) for p in watched}
    _require(before[str(watched[0])]==info['lineage']['checkpoint_sha256'] and
             before[str(watched[1])]==report28['v27_report_sha256'] and
             before[str(watched[2])]==report28['v27_base_archive_sha256'] and
             spine.adapter_identity(Path(info['lineage']['adapter_dir']))==
             info['lineage']['adapter_sha256'],'FROZEN_LINEAGE_MISMATCH')
    spine.cache_policy(args.allow_model_download)
    _,tok,model=gamer.model_components(info['lineage']['model_id'],args.device)
    model.eval();model.requires_grad_(False)
    _require(all(not p.requires_grad for p in model.parameters()),'QWEN_NOT_FROZEN')
    # Capture every candidate for new TRAIN perturbations and heldout TRAIN.
    # Never touch DEV until training/selection is complete.
    new_meta,new_vectors=v27._capture_variant(torch,tok,model,new_train,
           variant='base',device=args.device,batch=args.capture_batch,
           max_input_tokens=args.max_input_tokens,max_candidates=args.max_candidates,
           max_candidate_tokens=args.max_candidate_tokens)
    new_by,new_gold=v27._gold_candidate_ids(new_train,new_meta)
    new_features=v28._case_features(new_train,new_meta,new_vectors[v28.STAGE],
           new_by,new_gold,list(range(len(new_train))),base_weight,use_context=True)
    candidate,history,training=_train_from_v28(torch,protected,old_role,new_features,
           v28_anchor,steps=args.steps,lr=args.lr,batch_size=args.batch_size,
           eval_every=args.eval_every,max_halvings=args.max_halvings,seed=args.seed)
    # The held-out TRAIN wording never enters fitting or checkpoint selection.
    hold_meta,hold_vec=v27._capture_variant(torch,tok,model,heldout,
           variant='base',device=args.device,batch=args.capture_batch,
           max_input_tokens=args.max_input_tokens,max_candidates=args.max_candidates,
           max_candidate_tokens=args.max_candidate_tokens)
    hold_by,hold_gold=v27._gold_candidate_ids(heldout,hold_meta)
    del model;gc.collect()
    if torch.cuda.is_available():torch.cuda.empty_cache()
    _require(all(v19._sha(Path(p))==digest for p,digest in before.items()) and
             spine.adapter_identity(Path(info['lineage']['adapter_dir']))==
             info['lineage']['adapter_sha256'],'SOURCES_MODIFIED')
    tok_fit=v27._fit_token_centroids({v28.STAGE:vec},meta,gold,fit,v28.STAGE)
    records=[]
    original_scopes={'protected_train':list(range(len(train))),
       'heldout_train':hold,'original_dev':list(range(len(train),len(rows))),
       'stress_dev':list(range(len(rows),len(original)))}
    for scope,indices in original_scopes.items():
        for name,theta in [('v28_hybrid',v28_anchor),('v29_hybrid',candidate),
                           ('v28_neural_control',deltas['neural_only'])]:
            records+=_score(original,meta,vec,by_case,gold,indices,base_weight,
                      theta,tok_fit,tok,scope,name)
    for scope,entries,m,v,b,g,indices in (
          ('v28_augmented_train',old_aug,old_meta,old_vec,old_by,old_gold,
             list(range(len(old_aug)))),
          ('new_role_train',new_train,new_meta,new_vectors[v28.STAGE],new_by,new_gold,
             list(range(len(new_train)))),
          ('new_role_heldout_train',heldout,hold_meta,hold_vec[v28.STAGE],
             hold_by,hold_gold,list(range(len(heldout))))):
        for name,theta in [('v28_hybrid',v28_anchor),('v29_hybrid',candidate)]:
            records+=_score(entries,m,v,b,g,indices,base_weight,theta,tok_fit,tok,
                     scope,name)
    summary=v28._summarize(records)
    lookup={(s['scope'],s['kind'],s['policy']):s for s in summary}
    dev_base=lookup[('original_dev','original','v28_hybrid')]
    dev_new=lookup[('original_dev','original','v29_hybrid')]
    orig_base=lookup[('protected_train','original','v28_hybrid')]
    orig_new=lookup[('protected_train','original','v29_hybrid')]
    stress_base=lookup[('stress_dev','role_reordered','v28_hybrid')]
    stress_new=lookup[('stress_dev','role_reordered','v29_hybrid')]
    _require(stress_new['cases']>0 and stress_base['cases']==stress_new['cases'],
             'STRESS_BASELINE_MISSING')
    other_stress=[(lookup[('stress_dev',kind,'v28_hybrid')],
                   lookup[('stress_dev',kind,'v29_hybrid')])
             for kind in ('altered_wording','distractor_prefix','novel_operands')]
    heldout_retain=all(lookup[('new_role_heldout_train',kind,'v29_hybrid')]['exact_difference']
            >=lookup[('new_role_heldout_train',kind,'v28_hybrid')]['exact_difference']
            for kind in KINDS)
    aug_base=[x for x in records if x['scope']=='v28_augmented_train' and x['policy']=='v28_hybrid']
    aug_new=[x for x in records if x['scope']=='v28_augmented_train' and x['policy']=='v29_hybrid']
    protected_exact_loss=sum(bool(b['exact_difference']) and not n['exact_difference']
                             for b,n in zip(aug_base,aug_new))
    protected_selection_loss=v28._retention(protected,candidate,protected_mask)
    _require(protected_selection_loss==0 and protected_exact_loss==0,
             'PROTECTED_ACQUIRED_CASES_REGRESSED')
    gates={'prior_original_train_retained':orig_new['exact_difference']>=orig_base['exact_difference'],
       'all_v28_correct_train_selections_retained':protected_selection_loss==0,
       'all_v28_correct_augmented_exact_answers_retained':protected_exact_loss==0,
       'original_dev_144_exact':dev_new['exact_difference']==dev_base['exact_difference']==len(dev),
       'original_dev_zero_line_retained':dev_new['zero_exact']==dev_base['zero_exact']==dev_base['zero_total'],
       'original_dev_six_groups_retained':dev_new['complete_six_exact']==dev_base['complete_six_exact'],
       'TRAIN_role_reversal_improved':training['final_original_role_correct']>training['v28_original_role_correct']
               or training['new_role_final']>training['new_role_initial'],
       'stress_reversal_improved':stress_new['exact_difference']>stress_base['exact_difference'],
       'non_reversal_stress_unchanged':all(a['exact_difference']==b['exact_difference']
                           for a,b in other_stress),
       'heldout_role_train_nonregression':heldout_retain,
       'full_stress_zero_line':all(x['zero_exact']==x['zero_total'] for x in summary
            if x['scope']=='stress_dev' and x['policy']=='v29_hybrid'),
       'no_TEST_used':True}
    acceptance={'all_gates_met':all(gates.values()),'gates':gates,
       'promotion_authorized':False,'v28_stress_reversal_exact':stress_base['exact_difference'],
       'v29_stress_reversal_exact':stress_new['exact_difference'],
       'unseen_token_identity_certified':False}
    out.mkdir(parents=True,exist_ok=False)
    from tools.nanojev_gamer_v1_conflict_projection_v10 import _csv
    _csv(out/'role_training_history.csv',history)
    _csv(out/'role_comparison.csv',summary)
    _csv(out/'role_case_results.csv',records)
    checkpoint='disposable_v29_hybrid_role_candidate.pt'
    torch.save({'schema':SCHEMA,'production':False,'base_stage':v28.STAGE,
        'v28_checkpoint_sha256':before[str(watched[4])],
        'base_weight':torch.from_numpy(base_weight),
        'hybrid_v28_anchor':torch.from_numpy(v28_anchor),
        'hybrid_v29_candidate':torch.from_numpy(candidate),
        'neural_only_v28_unchanged':torch.from_numpy(deltas['neural_only']),
        'acceptance':acceptance},out/checkpoint)
    report={'schema':SCHEMA,'status':'ROLE_REVERSAL_TRAINING_COMPLETE_NO_PROMOTION',
        'algorithm':'V28_HYBRID_CONSTRAINED_TRAIN_ONLY_ROLE_REVERSAL_CONTINUATION',
        'frozen_lineage':info['lineage'],'source_dataset_sha256':info['dataset_sha256'],
        'v27_report_sha256':before[str(watched[1])],
        'v28_report_sha256':before[str(watched[3])],
        'v28_disposable_checkpoint_sha256':before[str(watched[4])],
        'v28_augmented_archive_sha256':before[str(watched[5])],
        'v28_augmented_metadata_sha256':before[str(watched[6])],
        'train_cases':len(train),'dev_cases':len(dev),'new_role_train_cases':len(new_train),
        'heldout_role_train_cases':len(heldout),
        'v28_augmented_train_cases':len(old_aug),
        'previously_correct_v28_original_train':int(protected_mask[:len(train)].sum()),
        'previously_correct_v28_augmented_train':int(protected_mask[len(train):].sum()),
        'retention_losses':int(protected_selection_loss),
        'protected_augmented_exact_answer_losses':int(protected_exact_loss),
        'training':training,'acceptance':acceptance,'comparisons':summary,
        'heldout_groups':heldout_groups,
        'neural_only_checkpoint_unchanged':True,
        'selector_only_trainable_parameters':True,
        'no_qwen_lora_or_original_head_training':True,
        'production_weights_modified':False,'promotion_authorized':False,
        'dev_used_for_checkpoint_selection':False,'test_used_for_fitting':False,
        'original_task_bypass':'UNCHANGED_V6',
        'disposable_selector_checkpoint_sha256':v19._sha(out/checkpoint),
        'limits':['Stress DEV is a fixed predeclared V27 challenge; TRAIN family overlaps, not independent generalization',
                  'Only the V28 hybrid selector is trained; neural-only V28 remains a frozen control',
                  'Unseen tokenizer identities are NOT certified; V28 OOV challenge found none',
                  'Exact subtraction is a deterministic Fraction diagnostic, not learned numerical computation',
                  'No source weights or production routing changed; no promotion authorized']}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    print('V29_COMPLETE '+probe.canonical({'status':report['status'],
        'training':training,'acceptance':acceptance,
        'report':str(out/'report.json'),'promotion':False}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    for name in ('run','expanded-run','input','source-features','prior','v11-run',
         'v14-run','v19-run','v20-run','v21-run','v22-run','v23-run','v24-run',
         'v25-run','v26-run','v27-run','v28-run','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--max-input-tokens',type=int,default=1024)
    p.add_argument('--max-candidates',type=int,default=16)
    p.add_argument('--max-candidate-tokens',type=int,default=16)
    p.add_argument('--steps',type=int,default=200)
    p.add_argument('--lr',type=float,default=0.001)
    p.add_argument('--batch-size',type=int,default=48)
    p.add_argument('--eval-every',type=int,default=20)
    p.add_argument('--max-halvings',type=int,default=12)
    p.add_argument('--seed',type=int,default=73)
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    _run(parser().parse_args(argv));return 0

if __name__=='__main__':raise SystemExit(main())
