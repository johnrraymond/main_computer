#!/usr/bin/env python3
"""V10: diagnose hard-retention update vetoes; try conflict-aware projections.

The selected protected V6 dual-unaligned numerical specialist is the common
start for the V9-compatible hard-gate control and the projected-update arm.
Only synthetic TRAIN supplies gradients, the irreversible acquisition ledger,
and update acceptance. DEV ranks verified checkpoints; the repeatedly examined
TEST split is scored only after all training. Original-task routing, Qwen,
LoRA, and the production head remain untouched. No promotion is authorized.

A projection is only a first-order proposal. It is NEVER an exemption from the
full, actual-inference, per-TRAIN-answer hard-retention check. Rejections and
optimizer-state rollbacks are explicit and audited.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from collections import Counter
from fractions import Fraction
from pathlib import Path

from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA = 'nanojev.gamer.v1.conflict-projection.v10'
ARMS = ('strict_hard_gate', 'conflict_projected')


def _csv(path, rows):
    if not rows:
        raise ValueError('V10_EMPTY_AUDIT: ' + str(path))
    with Path(path).open('w', newline='', encoding='utf-8') as stream:
        fields = list(dict.fromkeys(k for r in rows for k in r))
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _full_train_correct(torch, net, spec, mu, sigma, device):
    import numpy as np
    rows, x, old, _, routes = v7._split_arrays(spec, 'train')
    predicted, _ = v6._predict(torch, net, x, old, routes, mu, sigma, device)
    truth = np.asarray([v7._signed(Fraction(str(r['target']))) for r in rows], dtype=np.int64)
    return predicted == truth, predicted, truth


def _set_displacement(torch, net, before, direction, alpha):
    """Set a *complete* state against the same anchor, never accumulate deltas."""
    pos = 0
    with torch.no_grad():
        for name, param in net.named_parameters():
            n = param.numel()
            step = direction[pos:pos + n].reshape(param.shape).to(param.device, dtype=param.dtype)
            param.copy_(before[name].to(param.device) + alpha * step)
            pos += n
    if pos != direction.numel():
        raise ValueError('V10_DISPLACEMENT_PARAMETER_MISMATCH')


def _parameter_displacement(net, before):
    import torch
    return torch.cat([(p.detach().cpu().float() - before[k].float()).reshape(-1)
                      for k, p in net.named_parameters()])


def _project_halfspaces(direction, gradients, passes, *, max_norm_ratio=1.0):
    """Cyclic orthogonal projection of a parameter step onto g_i·d >= 0.

    g_i is the gradient of the *protected true-sign logit margin*. This
    linearized constraint is deliberately conservative and may not reflect
    the confidence-gated, discontinuous deployed prediction. Actual hard
    prediction checks, not this approximation, decide acceptance.
    """
    import torch
    d = direction.detach().cpu().float().clone()
    originals = d.clone()
    if d.ndim != 1 or not bool(torch.isfinite(d).all()) or passes < 1:
        raise ValueError('V10_INVALID_PROJECTED_DIRECTION')
    informative = []
    for grad in gradients:
        g = grad.detach().cpu().float()
        if g.shape != d.shape or not bool(torch.isfinite(g).all()):
            raise ValueError('V10_INVALID_CONSTRAINT_GRADIENT')
        squared = torch.dot(g, g)
        if float(squared) > 1e-16:
            informative.append((g, squared))
    before = sum(float(torch.dot(g, originals)) < 0 for g, _ in informative)
    for _ in range(passes):
        for g, squared in informative:
            dot = torch.dot(g, d)
            if float(dot) < 0:
                d += (-dot / squared) * g
    orig_norm = torch.linalg.vector_norm(originals)
    new_norm = torch.linalg.vector_norm(d)
    if float(orig_norm) and float(new_norm) > float(orig_norm) * max_norm_ratio:
        d *= orig_norm * max_norm_ratio / new_norm
    if not bool(torch.isfinite(d).all()):
        raise ValueError('V10_NONFINITE_PROJECTED_DIRECTION')
    return d, {'constraints_informative': len(informative),
               'violations_before': before,
               'violations_after': sum(float(torch.dot(g, d)) < -1e-7 for g, _ in informative),
               'direction_cosine': (float(torch.dot(originals, d) / (orig_norm * torch.linalg.vector_norm(d)))
                                    if float(orig_norm) and float(torch.linalg.vector_norm(d)) else 0.0),
               'displacement_norm_before': float(orig_norm),
               'displacement_norm_after': float(torch.linalg.vector_norm(d))}


def _margin_gradients(torch, net, before, encoded_train, old_train,
                      truth_train, conflicts, *, device):
    """At the pre-update weights, differentiate one true-sign margin per case.

    The conflict list comes ONLY from retained TRAIN cases that a rejected
    proposal damaged. No DEV or TEST data enters this function.
    """
    net.load_state_dict(before, strict=True)
    net.train()
    # Only trainable parameters participate in the first-order constraint.
    # Frozen base parameters in a capacity-expansion model have no gradient.
    all_params = list(net.parameters())
    params = [p for p in all_params if p.requires_grad]
    results = []
    from torch.nn import functional as F
    for ix in conflicts:
        i = int(ix)
        x = encoded_train[i:i+1]
        old = old_train[i:i+1]
        cls = int(truth_train[i]) + 1
        residual = net(x)
        logits = joint._head_logits(torch, old, residual)
        # Differentiable true-sign versus best alternative logit margin.
        alternative = torch.cat([logits[0,:cls], logits[0,cls+1:]])
        margin = logits[0,cls] - alternative.max()
        grads = torch.autograd.grad(margin, params, allow_unused=True)
        by_parameter = {id(p): g for p, g in zip(params, grads)}
        flat = torch.cat([torch.zeros_like(p).reshape(-1)
                          if by_parameter.get(id(p)) is None
                          else by_parameter[id(p)].detach().reshape(-1)
                          for p in all_params]).cpu()
        if not torch.isfinite(flat).all():
            raise ValueError('V10_NONFINITE_CONFLICT_GRADIENT')
        results.append(flat)
    net.zero_grad(set_to_none=True)
    return results


def _loss_and_proposal(torch, net, optimizer, x, old, y, anchor,
                       *, group_count, replay_weight):
    """Exact V9 synthetic+triple+anchor objective and AdamW proposal."""
    from torch.nn import functional as F
    triples = [(6*k+j*3, 6*k+j*3+1, 6*k+j*3+2)
               for k in range(group_count) for j in range(2)]
    net.train();optimizer.zero_grad(set_to_none=True)
    main, _ = joint._training_loss(torch, net, x, old, y,
        torch.zeros(len(y), device=y.device, dtype=torch.bool), weight=1., source=False)
    triple = joint._triple_loss(torch, net, x, old, triples)
    if bool(anchor.any()):
        logits = joint._head_logits(torch, old, net(x))
        labels = torch.sign(y).long() + 1
        truth = logits.gather(1, labels[:,None]).squeeze(1)
        other = logits.clone().scatter(1, labels[:,None], -1e6).amax(1)
        anchor_loss = F.relu(1.0-(truth-other))[anchor].square().mean()
    else:
        anchor_loss = main.new_zeros(())
    loss = main + 2*triple + replay_weight*anchor_loss
    if not torch.isfinite(loss):
        raise ValueError('V10_NONFINITE_TRAIN_LOSS')
    loss.backward()
    torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
    optimizer.step()
    return float(loss.detach()), float(anchor_loss.detach())


def _conflicts(ledger, correct):
    import numpy as np
    return np.flatnonzero(ledger.mask & ~correct)


def _case_margins(torch, net, spec, mu, sigma, device, indices):
    """Read the actual bounded specialist logits/values for implicated TRAIN cases."""
    import numpy as np
    ix=np.asarray(indices,dtype=np.int64)
    if len(ix)==0:
        return {}
    rows,x,old,_,routes=v7._split_arrays(spec,'train')
    pred,value,delta=v6._predict(torch,net,np.asarray(x)[ix],
          np.asarray(old)[ix],np.asarray(routes)[ix],mu,sigma,device,
          return_delta=True)
    logits=joint._baseline_logits(np.asarray(old)[ix])+delta[:,1:]
    result={}
    for j,i in enumerate(ix):
        cls=v7._signed(Fraction(str(rows[int(i)]['target'])))+1
        alternatives=np.concatenate([logits[j,:cls],logits[j,cls+1:]])
        result[int(i)]={'true_class_margin':float(logits[j,cls]-max(alternatives)),
           'prediction':int(pred[j]),'value':float(value[j]),
           'residual_signed_log':float(delta[j,0]),
           'residual_logit_l2':float(np.linalg.norm(delta[j,1:]))}
    return result


def _victim_rows(rows, indices, predicted, truth, *, step, arm, stage, alpha,
                 trial_margins=None):
    out=[]
    for j in indices:
        i=int(j); r=rows[i]
        out.append({'step':step,'arm':arm,'stage':stage,'alpha':alpha,
          'case_id':r['id'],'group':r['group'],'surface':r['surface'],
          'prompt_sha256':r['prompt_sha256'],
          'target_sign':int(truth[i]),'proposed_sign':int(predicted[i]),
          'failure_type': ('zero_to_nonzero' if int(truth[i])==0 else
                           'nonzero_to_zero' if int(predicted[i])==0 else
                           'nonzero_sign_reversal'),
          **(trial_margins or {}).get(i,{})})
    return out


def _attempt_update(torch, net, optimizer, tensors, chosen, ledger, spec,
                    mu, sigma, *, device, step, arm, max_halvings,
                    replay_weight, projection_passes, max_constraints):
    """V9 update + exact failure census + optional halfspace repair.

    Atomicity: on rejection, restore *both* Adam moments and model weights;
    after projected acceptance, restore the pre-proposal optimizer moments so
    the rejected Adam direction cannot persist in optimizer state.
    """
    import numpy as np
    xfull, oldfull, yfull = tensors
    rows = spec['train_synthetic'][3]
    before = v9._copy_state(net)
    optimizer_before = copy.deepcopy(optimizer.state_dict())
    indices = np.concatenate([g[0] for g in chosen])
    anchor = torch.as_tensor(ledger.mask[indices], device=device, dtype=torch.bool)
    loss, anchor_loss = _loss_and_proposal(torch,net,optimizer,xfull[indices],
        oldfull[indices],yfull[indices],anchor,
        group_count=len(chosen),replay_weight=replay_weight)
    raw_direction = _parameter_displacement(net,before)
    proposed = v9._copy_state(net)
    if not all(torch.equal(before[k],proposed[k]) for k in before if not torch.is_floating_point(before[k])):
        raise ValueError('V10_UNEXPECTED_NONFLOAT_STATE_MUTATION')
    truth = np.asarray([v7._signed(Fraction(str(r['target']))) for r in rows],dtype=np.int64)
    traces=[]
    raw_evals=[]
    candidate=None
    for h in range(max_halvings+1):
        alpha=2.**(-h)
        _set_displacement(torch,net,before,raw_direction,alpha)
        correct, predicted, checked_truth = _full_train_correct(torch,net,spec,mu,sigma,device)
        if not np.array_equal(truth,checked_truth):
            raise ValueError('V10_TRAIN_TRUTH_CHANGED')
        lost = _conflicts(ledger,correct)
        raw_evals.append({'alpha':alpha,'lost':int(len(lost)),'victims':lost.tolist()})
        if h in (0,max_halvings) and len(lost):
            trial=_case_margins(torch,net,spec,mu,sigma,device,lost)
            traces.extend(_victim_rows(rows,lost,predicted,truth,step=step,arm=arm,
                           stage=('raw_full' if h==0 else 'raw_smallest'),alpha=alpha,
                           trial_margins=trial))
        if not len(lost):
            candidate=('raw',alpha,correct)
            break
    projected_evals=[]
    project_info={'constraints_informative':0,'violations_before':0,
                  'violations_after':0,'direction_cosine':None,
                  'displacement_norm_before':float(torch.linalg.vector_norm(raw_direction)),
                  'displacement_norm_after':None}
    if candidate is None and arm in ('conflict_projected', 'expanded_capacity_projected'):
        # The smallest rejected step isolates the boundary obstruction. Full
        # step casualties are still logged independently, never hidden.
        conflict_ids=raw_evals[-1]['victims']
        if len(conflict_ids)>max_constraints:
            # Deterministic, complete-group-covering prefix; if other victims
            # remain, the full hard gate still refuses an unsafe projection.
            conflict_ids=sorted(conflict_ids, key=lambda i:(rows[i]['group'],rows[i]['id']))[:max_constraints]
        gradients=_margin_gradients(torch,net,before,xfull,oldfull,truth,
                                   conflict_ids,device=device)
        projected,project_info=_project_halfspaces(raw_direction,gradients,
                                                  projection_passes)
        for h in range(max_halvings+1):
            alpha=2.**(-h)
            _set_displacement(torch,net,before,projected,alpha)
            correct,predicted,_=_full_train_correct(torch,net,spec,mu,sigma,device)
            lost=_conflicts(ledger,correct)
            projected_evals.append({'alpha':alpha,'lost':int(len(lost))})
            if h in (0,max_halvings) and len(lost):
                trial=_case_margins(torch,net,spec,mu,sigma,device,lost)
                traces.extend(_victim_rows(rows,lost,predicted,truth,step=step,arm=arm,
                         stage=('projected_full' if h==0 else 'projected_smallest'),alpha=alpha,
                         trial_margins=trial))
            if not len(lost):
                candidate=('projected',alpha,correct)
                break
    if candidate is None:
        net.load_state_dict(before,strict=True)
        optimizer.load_state_dict(optimizer_before)
        status='rejected'
        alpha=0.; correct=None
    else:
        status=candidate[0]
        alpha=candidate[1]
        correct=candidate[2]
        if status=='projected':
            # Adam's unprojected moment update was not actually committed.
            optimizer.load_state_dict(optimizer_before)
    if traces:
        end_state=v9._copy_state(net)
        net.load_state_dict(before,strict=True)
        index={r['id']:i for i,r in enumerate(rows)}
        baselines=_case_margins(torch,net,spec,mu,sigma,device,
                    sorted({index[r['case_id']] for r in traces}))
        net.load_state_dict(end_state,strict=True)
        for record in traces:
            anchor=baselines[index[record['case_id']]]
            record['before_predicted_sign']=anchor['prediction']
            record['before_true_class_margin']=anchor['true_class_margin']
            record['trial_true_class_margin']=record.pop('true_class_margin')
            record['true_class_margin_shift']=(record['trial_true_class_margin']-
                                                record['before_true_class_margin'])
            record['before_value']=anchor['value']
            record['trial_value']=record.pop('value')
            record['before_residual_signed_log']=anchor['residual_signed_log']
            record['trial_residual_signed_log']=record.pop('residual_signed_log')
            record['before_residual_logit_l2']=anchor['residual_logit_l2']
            record['trial_residual_logit_l2']=record.pop('residual_logit_l2')
    raw_full=raw_evals[0]['victims']
    raw_small=raw_evals[-1]['victims']
    report={'step':step,'arm':arm,'status':status,'accepted':int(candidate is not None),
      'alpha':float(alpha),'loss':loss,'anchor_loss':anchor_loss,
      'raw_full_lost':len(raw_full),'raw_smallest_lost':len(raw_small),
      'raw_full_zero_lost':sum(truth[i]==0 for i in raw_full),
      'raw_full_negative_lost':sum(truth[i]<0 for i in raw_full),
      'raw_full_positive_lost':sum(truth[i]>0 for i in raw_full),
      'raw_full_lost_groups':len({rows[i]['group'] for i in raw_full}),
      'raw_smallest_zero_lost':sum(truth[i]==0 for i in raw_small),
      'raw_smallest_negative_lost':sum(truth[i]<0 for i in raw_small),
      'raw_smallest_positive_lost':sum(truth[i]>0 for i in raw_small),
      'raw_smallest_lost_groups':len({rows[i]['group'] for i in raw_small}),
      'projection_cases_considered':min(len(raw_small),max_constraints) if projected_evals else 0,
      'projection_constraints_truncated':max(0,len(raw_small)-max_constraints) if projected_evals else 0,
      'projected_full_lost':projected_evals[0]['lost'] if projected_evals else None,
      'projected_smallest_lost':projected_evals[-1]['lost'] if projected_evals else None,
      'raw_attempts_evaluated':len(raw_evals),'projected_attempts_evaluated':len(projected_evals),
      **project_info}
    return report,correct,traces


def _fit_arm(torch,spec,starting,mu,sigma,*,arm,device,steps,eval_every,
             lr,seed,replay_weight,max_halvings,projection_passes,max_constraints,
             model_builder=None,capture_terminal=False):
    import numpy as np
    groups, train_sha=v9._groups_and_fingerprints(spec)
    tx,old,y,rows,routes=spec['train_synthetic']
    sx,sold,_,sroutes=spec['train_source']
    torch.manual_seed(seed)
    net=(model_builder(torch) if model_builder is not None else
         v6._build_aligned_model(torch,protected=True)).to(device)
    net.load_state_dict(starting,strict=True)
    ledger=v9.MathematicalLedger(v9._correct_train(torch,net,spec,mu,sigma,device),rows,groups)
    xfull=torch.from_numpy(v6._encode(tx,old,routes,mu,sigma)).to(device)
    oldfull=torch.as_tensor(old,device=device,dtype=torch.float32)
    yfull=torch.as_tensor(y,device=device,dtype=torch.float32)
    truth=np.asarray([v7._signed(Fraction(str(r['target']))) for r in rows],dtype=np.int64)
    if not np.array_equal(np.sign(y),truth):
        raise ValueError('V10_TRAIN_LABEL_FLOAT_SIGN_MISMATCH')
    opt=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    rng=np.random.default_rng(seed)
    def assess():
        ret,dev=v6._evaluate(torch,net,spec,mu,sigma,device)
        if ret['status']!='PASS':
            raise ValueError('V10_ORIGINAL_RETENTION_GATE_FAILED')
        return ret,dev,joint._rank(ret,dev)
    ret,dev,rank=assess()
    best={'step':0,'weights':v9._copy_state(net),'ledger_mask':ledger.mask.copy(),
          'acquired_step':ledger.acquired_step.copy(),'retention':ret,'dev':dev,'rank':rank}
    metrics=[{'step':0,'arm':arm,'accepted':1,'train_locked':int(ledger.mask.sum()),
              'dev_sign':dev['all']['balanced_sign_accuracy'],
              'dev_six':dev['cross_format']['complete_six_case_groups']}]
    attempts=[];victims=[]
    for step in range(1,steps+1):
        new=rng.choice(len(groups),size=2,replace=True)
        replay=ledger.sample_replay_groups(rng,2)
        chosen=[groups[int(i)] for i in np.concatenate([new,replay])]
        outcome,correct,rows_lost=_attempt_update(torch,net,opt,(xfull,oldfull,yfull),
            chosen,ledger,spec,mu,sigma,device=device,step=step,arm=arm,
            max_halvings=max_halvings,replay_weight=replay_weight,
            projection_passes=projection_passes,max_constraints=max_constraints)
        victims.extend(rows_lost)
        if outcome['accepted']:
            event=ledger.acquire(correct,step)
        else:
            event={'new_correct':0,**v9._memory_summary(ledger.mask,groups)}
        outcome.update({'new_correct':event['new_correct'],
                        'train_locked':event['correct_cases'],
                        'train_complete_six':event['complete_six_case_groups']})
        attempts.append(outcome)
        if step%eval_every==0 or step==steps:
            if not ledger.accepts(v9._correct_train(torch,net,spec,mu,sigma,device)):
                raise ValueError('V10_ACTUAL_TRAIN_RETENTION_FAILED')
            ret,dev,rank=assess()
            metrics.append({'step':step,'arm':arm,
                'updates_accepted':sum(x['accepted'] for x in attempts),
                'raw_accepted':sum(x['status']=='raw' for x in attempts),
                'projection_rescues':sum(x['status']=='projected' for x in attempts),
                'updates_rejected':sum(x['status']=='rejected' for x in attempts),
                'train_locked':int(ledger.mask.sum()),
                'dev_sign':dev['all']['balanced_sign_accuracy'],
                'dev_six':dev['cross_format']['complete_six_case_groups']})
            if rank>best['rank']:
                best={'step':step,'weights':v9._copy_state(net),
                      'ledger_mask':ledger.mask.copy(),
                      'acquired_step':ledger.acquired_step.copy(),
                      'retention':ret,'dev':dev,'rank':rank}
            print('V10_PROGRESS '+probe.canonical({
              'arm':arm,'step':step,'accepted':metrics[-1]['updates_accepted'],
              'projection_rescues':metrics[-1]['projection_rescues'],
              'rejected':metrics[-1]['updates_rejected'],
              'train_locked':int(ledger.mask.sum()),
              'dev_sign':dev['all']['balanced_sign_accuracy'],
              'dev_six':dev['cross_format']['complete_six_case_groups']}),flush=True)
    final_run_summary=v9._memory_summary(ledger.mask,groups)
    terminal_snapshot=({'step':steps,'weights':v9._copy_state(net),
                        'ledger_mask':ledger.mask.copy(),
                        'acquired_step':ledger.acquired_step.copy(),
                        'retention':ret,'dev':dev, 'rank':rank}
                       if capture_terminal else None)
    net.load_state_dict(best['weights'],strict=True)
    ledger.mask=best['ledger_mask'].copy()
    ledger.acquired_step=best['acquired_step'].copy()
    if not ledger.accepts(v9._correct_train(torch,net,spec,mu,sigma,device)):
        raise ValueError('V10_SELECTED_CHECKPOINT_BROKE_TRAIN_LEDGER')
    original_class,original_value=v6._predict(torch,net,sx,sold,sroutes,mu,sigma,device)
    import numpy as np
    if not (np.array_equal(original_value,np.asarray(sold,dtype=np.float64)) and
            np.array_equal(original_class,lab._classify_continuous(sold))):
        raise ValueError('V10_FROZEN_ORIGINAL_PASSTHROUGH_DRIFT')
    deepest=[r for r in victims if r['stage']=='raw_smallest']
    raw_first=[r for r in victims if r['stage']=='raw_full']
    stats={'arm':arm,'seed':seed,'train_capture_sha256':train_sha,
       'raw_full_conflicts_by_surface':dict(Counter(r['surface'] for r in raw_first)),
       'raw_smallest_conflicts_by_surface':dict(Counter(r['surface'] for r in deepest)),
       'raw_smallest_conflicts_by_failure_type':dict(Counter(r['failure_type'] for r in deepest)),
       'top_conflicting_groups':Counter(r['group'] for r in deepest).most_common(15),
       'steps_with_raw_full_conflict':sum(a['raw_full_lost']>0 for a in attempts),
       'steps_with_smallest_raw_conflict':sum(a['raw_smallest_lost']>0 for a in attempts),

       'initial_train':ledger.initial,
       'full_run_final_train':final_run_summary,
       'selected_train':v9._memory_summary(ledger.mask,groups),
       'selected_step':best['step'],
       'accepted_updates':sum(x['accepted'] for x in attempts),
       'rejected_updates':sum(x['status']=='rejected' for x in attempts),
       'projection_rescues':sum(x['status']=='projected' for x in attempts),
       'raw_accepts':sum(x['status']=='raw' for x in attempts),
       'retained_all_acquired_training_answers':True,
       'optimizer_state_rollback_on_projection':'RESTORE_PRE_PROPOSAL',
       'projection_is_first_order_proposal_not_acceptance':True,
       'hard_full_train_gate_unchanged':True,
       'DEV_fitting':False,'TEST_fitting_or_selection':False}
    if capture_terminal:
        stats['terminal_snapshot']=terminal_snapshot
    return net,best,ledger,attempts,victims,metrics,stats


def _validate(args):
    for field in ('run','expanded_run','input','source_features','prior','v9_run','output'):
        if getattr(args,field,None) is None:
            raise ValueError('V10_MISSING_REQUIRED_ARGUMENT: '+field)
    if (args.steps<1 or args.steps>20000 or args.eval_every<1 or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.replay_weight) or args.replay_weight<0 or
        not 0<=args.max_halvings<=12 or not 1<=args.projection_passes<=20 or
        not 1<=args.max_constraints<=128):
        raise ValueError('V10_INVALID_CONFIGURATION')


def _verify_v9_record(torch, path, args, data, prior_hash, capture_sha):
    """Read-only historical V9 comparator with complete provenance checks."""
    base=Path(path)
    report_path=base/'report.json'
    checkpoint_path=base/'monotonic_retention_replay_candidate.pt'
    if not report_path.is_file() or not checkpoint_path.is_file():
        raise ValueError('V10_PRIOR_V9_REPORT_OR_CHECKPOINT_MISSING')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    cp=torch.load(checkpoint_path,map_location='cpu',weights_only=True)
    source=data['probe_info']['lineage']
    expected={'steps':args.steps,'eval_every':args.eval_every,'lr':args.lr,
              'seed':args.seed,'replay_weight':args.replay_weight,
              'max_halvings':args.max_halvings}
    if (report.get('schema')!=v9.SCHEMA or
        report.get('status')!='EXPERIMENT_COMPLETE_NO_PROMOTION' or
        report.get('config')!=expected or
        report.get('source_lineage')!=source or
        report.get('synthetic_dataset_sha256')!=data['probe_info']['dataset_sha256'] or
        report.get('original_train_cache_sha256')!=data['train_cache']['sha256'] or
        report.get('train_capture_sha256')!=capture_sha or
        cp.get('schema')!=v9.SCHEMA or
        cp.get('arm')!='monotonic_retention_replay' or
        cp.get('status')!='DISPOSABLE_NON_PRODUCTION' or
        cp.get('promotion_authorized') is not False or
        cp.get('protected_original_passthrough') is not True or
        cp.get('prior_v6_sha256')!=prior_hash or
        cp.get('frozen_lineage')!=source or
        cp.get('train_capture_sha256')!=capture_sha or
        cp.get('synthetic_dataset_sha256')!=data['probe_info']['dataset_sha256'] or
        cp.get('original_train_cache_sha256')!=data['train_cache']['sha256']):
        raise ValueError('V10_HISTORICAL_V9_PROVENANCE_OR_SETTINGS_MISMATCH')
    memory=cp.get('mathematical_train_retention_ledger')
    if (not isinstance(memory,dict) or memory.get('source_split')!='SYNTHETIC_TRAIN_ONLY' or
        memory.get('train_capture_sha256')!=capture_sha or
        memory.get('selected_step')!=cp.get('selected_step')):
        raise ValueError('V10_HISTORICAL_V9_LEDGER_PROVENANCE_MISMATCH')
    return {'report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),
        'checkpoint_sha256':hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        'historical_v9_selected_step':cp['selected_step'],
        'historical_v9_training':report['mathematical_retention'],
        'historical_v9_candidates':[{ 'id':c['id'],
               'dev_sign':c['dev']['all']['balanced_sign_accuracy'],
               'dev_six':c['dev']['cross_format']['complete_six_case_groups'],
               'train':c['train']} for c in report['candidates']]}


def run(args):
    import numpy as np
    import torch
    _validate(args)
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('V10_CUDA_UNAVAILABLE')
    out=lab._output(args.output,[Path(x) for x in
        (args.run,args.expanded_run,args.input,args.source_features,args.prior,args.v9_run)])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    net,mu,sd,cp=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    net=net.to(args.device)
    initial=v9._copy_state(net)
    groups,capture_sha=v9._groups_and_fingerprints(spec)
    prior_hash=hashlib.sha256(Path(args.prior).read_bytes()).hexdigest()
    historic=_verify_v9_record(torch,args.v9_run,args,data,prior_hash,capture_sha)
    starting_correct=v9._correct_train(torch,net,spec,mu,sd,args.device)
    reference_retention,reference_dev=v6._evaluate(torch,net,spec,mu,sd,args.device)
    if reference_retention['status']!='PASS':
        raise ValueError('V10_V6_REFERENCE_ORIGINAL_RETENTION_FAILED')
    initial_count=int(starting_correct.sum())
    results={}; artifacts={}
    for arm in ARMS:
        model,best,ledger,attempts,victims,metrics,stats=_fit_arm(torch,spec,
            initial,mu,sd,arm=arm,device=args.device,steps=args.steps,
            eval_every=args.eval_every,seed=args.seed,lr=args.lr,
            replay_weight=args.replay_weight,max_halvings=args.max_halvings,
            projection_passes=args.projection_passes,max_constraints=args.max_constraints)
        results[arm]={'model':model,'best':best,'ledger':ledger,
                      'attempts':attempts,'victims':victims,'metrics':metrics,'stats':stats}
        if stats['initial_train']['correct_cases']!=initial_count:
            raise ValueError('V10_ARMS_DID_NOT_SHARE_V6_INITIAL_COMPETENCE')
    evaluated=[]
    for arm in ARMS:
        obj=results[arm];model=obj['model']
        original_ret,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
        _,test=v6._evaluate(torch,model,spec,mu,sd,args.device,'test_synthetic')
        model_train=v9._correct_train(torch,model,spec,mu,sd,args.device)
        if original_ret['status']!='PASS' or not obj['ledger'].accepts(model_train):
            raise ValueError('V10_FINAL_SELECTED_RETENTION_FAILED')
        evaluated.append({'arm':arm,'selected_step':obj['best']['step'],
         'original_dev_retention':original_ret,'dev':dev,
         'previously_seen_test_diagnostic_only':test,
         'train':v9._memory_summary(model_train,groups),
         'training_diagnostics':obj['stats'],'promotion_authorized':False})
    control,repaired=evaluated
    # DEV can choose only a checkpoint satisfying TRAIN ledger + original guard.
    winner=max(evaluated,key=lambda r:joint._rank(r['original_dev_retention'],r['dev']))
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
      'frozen_lineage':data['probe_info']['lineage'],
      'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
      'original_train_cache_sha256':data['train_cache']['sha256'],
      'v6_prior_sha256':prior_hash,'training_capture_sha256':capture_sha,
      'v9_historical_reference':historic,
      'selected_candidate':winner['arm'],'candidates':evaluated,
      'source_dev_used_for_training':False,'synthetic_dev_used_for_training':False,
      'previously_seen_test_used_for_training_or_selection':False,
      'preexisting_v9_test_results_used_for_selection':False,
      'hard_train_retention_gate':'EXACT_INFERENCE_ALL_ACQUIRED_CORRECT_CASES',
      'projection':'FIRST_ORDER_TRUE_SIGN_LOGIT_MARGIN_HALFSPACE',
      'projection_acceptance':'REQUIRES_FULL_TRAIN_EXACT_PROTECTED_PREDICTION',
      'v6_original_task_bypass':'UNCHANGED',
      'general_production_router':'NOT_CERTIFIED',
      'new_model_weights':True,'production_weights_modified':False,
      'promotion_authorized':False,
      'config':{'steps':args.steps,'eval_every':args.eval_every,'lr':args.lr,
        'seed':args.seed,'replay_weight':args.replay_weight,
        'max_halvings':args.max_halvings,
        'projection_passes':args.projection_passes,
        'max_constraints':args.max_constraints}}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    for arm,obj in results.items():
        _csv(out/(arm+'_attempts.csv'),obj['attempts'])
        if obj['victims']:
            _csv(out/(arm+'_conflict_cases.csv'),obj['victims'])
        else:
            _csv(out/(arm+'_conflict_cases.csv'),[{'arm':arm,'stage':'NONE','case_id':'NONE'}])
        _csv(out/(arm+'_checkpoints.csv'),obj['metrics'])
        _csv(out/(arm+'_train_ledger.csv'),obj['ledger'].as_csv())
        for split in ('train','dev','test'):
            rows=v7._case_atlas(torch,obj['model'],spec,mu,sd,args.device,split)
            label='previously_seen_test' if split=='test' else split
            _csv(out/f'{arm}_cases_{label}.csv',rows)
            _csv(out/f'{arm}_groups_{label}.csv',v7._group_atlas(rows))
    _csv(out/'comparison.csv',[{'arm':r['arm'],'selected_step':r['selected_step'],
       'updates_accepted':r['training_diagnostics']['accepted_updates'],
       'projection_rescues':r['training_diagnostics']['projection_rescues'],
       'rejected_steps':r['training_diagnostics']['rejected_updates'],
       'train_correct':r['train']['correct_cases'],
       'train_six':r['train']['complete_six_case_groups'],
       'dev_sign':r['dev']['all']['balanced_sign_accuracy'],
       'dev_six':r['dev']['cross_format']['complete_six_case_groups'],
       'previously_seen_test_sign_diagnostic_only':r['previously_seen_test_diagnostic_only']['all']['balanced_sign_accuracy'],
       'previously_seen_test_six_diagnostic_only':r['previously_seen_test_diagnostic_only']['cross_format']['complete_six_case_groups'],
       'original_retention':r['original_dev_retention']['status']}
       for r in evaluated])
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                             allow_nan=False)+'\n',encoding='utf-8')
    for arm,obj in results.items():
        payload={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
            'arm':arm,'weights':obj['best']['weights'],
            'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
            'frozen_lineage':data['probe_info']['lineage'],
            'prior_v6_sha256':prior_hash,'train_capture_sha256':capture_sha,
            'mathematical_train_retention_ledger':{
                'source_split':'SYNTHETIC_TRAIN_ONLY',
                'train_capture_sha256':capture_sha,
                'rows':obj['ledger'].as_csv(),
                'selected_step':obj['best']['step'],
                'hard_retention_pass':True},
            'protected_original_passthrough':True,'capture_bound_router_only':True,
            'promotion_authorized':False,'selected_step':obj['best']['step']}
        torch.save(payload,out/(arm+'_candidate.pt'))
        if arm==winner['arm']:
            torch.save(payload,out/'disposable_candidate.pt')
    for r in evaluated:
        print('V10_RESULT '+probe.canonical({'arm':r['arm'],
           'selected_step':r['selected_step'],
           'accepted':r['training_diagnostics']['accepted_updates'],
           'projected_rescues':r['training_diagnostics']['projection_rescues'],
           'rejected':r['training_diagnostics']['rejected_updates'],
           'train_correct':r['train']['correct_cases'],
           'raw_smallest_conflicts_by_type':r['training_diagnostics']['raw_smallest_conflicts_by_failure_type'],
           'steps_with_raw_conflict':r['training_diagnostics']['steps_with_smallest_raw_conflict'],
           'top_conflicting_train_groups':r['training_diagnostics']['top_conflicting_groups'][:3],
           'dev_sign':r['dev']['all']['balanced_sign_accuracy'],
           'dev_six':r['dev']['cross_format']['complete_six_case_groups'],
           'previously_seen_test_sign_diagnostic_only':r['previously_seen_test_diagnostic_only']['all']['balanced_sign_accuracy']}),flush=True)
    print('V10_COMPLETE '+probe.canonical({'selected':winner['arm'],
         'report':str(out/'report.json'),
         'production_weights_modified':False,'promotion':False}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--prior',type=Path,help='V6 selected dual_protected_unaligned checkpoint')
    p.add_argument('--v9-run',type=Path,help='Verified historical V9 report and checkpoint')
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=300)
    p.add_argument('--eval-every',type=int,default=30)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    p.add_argument('--projection-passes',type=int,default=4)
    p.add_argument('--max-constraints',type=int,default=24)
    return p


def main(argv=None):
    run(parser().parse_args(argv))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
