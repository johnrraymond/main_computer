#!/usr/bin/env python3
"""V9: strictly monotonic mathematical TRAIN competence through replay + rollback.

Two matched 300-step studies start from the exact selected V6 protected
unaligned dual-projector checkpoint: V7 ordinary continuation and V9 retention
replay. Neither Qwen, LoRA nor production head is trained or overwritten.

V9's memory consists EXCLUSIVELY of certified synthetic TRAIN predictions that
were correct at step zero or became correct at an accepted training step. Once
acquired, a correct answer cannot be lost at any accepted update. Full six-case
training groups are always sampled as units, and completed triples/groups are
tracked without replacing per-answer preservation. A backtracked update is
accepted only if every ledger answer remains correct under the actual V6
protected inference policy. DEV is for checkpoint selection; previously seen
TEST is reported for diagnosis only and is not used by the training loop.

This is a bounded, capture-specific experiment, NOT generalization proof or
a production routing/promotion capability.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
from fractions import Fraction
from collections import Counter
from pathlib import Path

from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA = 'nanojev.gamer.v1.math-retention.v9'
ARMS = ('uniform_continuation', 'monotonic_retention_replay')


def _copy_state(net):
    return {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}


def _groups_and_fingerprints(spec):
    """Validate the independent TRAIN group and prompt identities."""
    import numpy as np
    x, old, target, rows, routes = spec['train_synthetic']
    groups = joint._complete_training_groups(rows, target)
    if not groups or len(rows) != len(x) or len(rows) != len(routes):
        raise ValueError('V9_INCOMPLETE_TRAIN_GROUPS')
    ids = []
    fingerprints = []
    seen_ids, seen_prompts, seen_groups = set(), set(), set()
    for group, _ in groups:
        if len(group) != 6:
            raise ValueError('V9_INCOMPLETE_SIX_CASE_GROUP')
        members = [rows[int(i)] for i in group]
        key = members[0]['group']
        if key in seen_groups or any(r['group'] != key for r in members):
            raise ValueError('V9_DUPLICATE_OR_MIXED_GROUP')
        seen_groups.add(key)
        if [v7._signed(Fraction(str(r['target']))) for r in members] != [-1,0,1,-1,0,1]:
            raise ValueError('V9_SIGN_ORDER_CHANGED')
        if not np.array_equal(np.asarray(routes)[list(group)], [0,0,0,1,1,1]):
            raise ValueError('V9_CERTIFIED_TRAIN_ROUTING_CHANGED')
        if any(members[j]['target'] != members[j+3]['target'] for j in range(3)):
            raise ValueError('V9_CROSS_FORMAT_TARGET_MISMATCH')
        for row in members:
            rid = row['id']
            sha = row['prompt_sha256']
            if rid in seen_ids or sha in seen_prompts or len(sha) != 64:
                raise ValueError('V9_DUPLICATE_OR_UNVERIFIED_CASE')
            seen_ids.add(rid); seen_prompts.add(sha)
            ids.append(rid)
            fingerprints.append(sha)
    if len(ids) != len(rows):
        raise ValueError('V9_GROUPS_DO_NOT_COVER_TRAIN')
    # joint._complete_training_groups may return non-contiguous positions.
    by_id = [r['id'] for r in rows]
    if set(by_id) != set(ids):
        raise ValueError('V9_GROUP_ORDER_INCONSISTENT')
    return groups, hashlib.sha256(probe.canonical([
        {'id': r['id'], 'prompt_sha256': r['prompt_sha256'],
         'group': r['group'], 'target': str(r['target'])}
        for r in rows]).encode()).hexdigest()


def _correct_train(torch, net, spec, mu, sigma, device):
    """Only synthetic TRAIN: use real protected predictions, including abstention."""
    import numpy as np
    rows, x, old, y, routes = v7._split_arrays(spec, 'train')
    cls, _ = v6._predict(torch, net, x, old, routes, mu, sigma, device)
    actual = np.asarray([v7._signed(Fraction(str(r['target']))) for r in rows], dtype=np.int64)
    correct = np.asarray(cls, dtype=np.int64) == actual
    return correct


def _memory_summary(mask, groups):
    import numpy as np
    correct = np.asarray(mask, dtype=bool)
    triples = 0; six = 0; active_groups = 0
    for positions, _ in groups:
        p = correct[np.asarray(positions, dtype=np.int64)]
        active_groups += int(p.any())
        triples += int(p[:3].all()) + int(p[3:].all())
        six += int(p.all())
    return {'correct_cases': int(correct.sum()), 'complete_triples': int(triples),
            'complete_six_case_groups': int(six), 'groups_with_any_correct':int(active_groups)}


class MathematicalLedger:
    """TRAIN-only monotonic commitments, never populated by DEV or TEST."""
    def __init__(self, initial_correct, rows, groups):
        import numpy as np
        initial = np.asarray(initial_correct, dtype=bool)
        if initial.shape != (len(rows),) or not initial.any():
            raise ValueError('V9_LEDGER_EXPECTS_CORRECT_TRAIN_ANCHORS')
        self.rows = rows
        self.groups = groups
        self.mask = initial.copy()
        self.acquired_step = np.full(len(rows), -1, dtype=np.int64)
        self.acquired_step[initial] = 0
        self.initial = _memory_summary(initial, groups)
        self.events = [{'step':0, 'new_correct':int(initial.sum()),
                        **self.initial}]
        self.checked_steps = 0

    def accepts(self, correct):
        import numpy as np
        candidate = np.asarray(correct,dtype=bool)
        if candidate.shape != self.mask.shape:
            raise ValueError('V9_LEDGER_SIZE_CHANGED')
        return bool(np.all(candidate[self.mask]))

    def acquire(self, correct, step):
        import numpy as np
        candidate = np.asarray(correct,dtype=bool)
        if not self.accepts(candidate):
            raise ValueError('V9_HARD_MATHEMATICAL_RETENTION_VIOLATED')
        if step <= 0 or (self.events and step <= self.events[-1]['step']):
            raise ValueError('V9_NONMONOTONIC_STEP')
        newly = candidate & ~self.mask
        self.acquired_step[newly] = step
        self.mask |= newly
        rec = {'step':step, 'new_correct':int(newly.sum()),
               **_memory_summary(self.mask,self.groups)}
        self.events.append(rec)
        self.checked_steps += 1
        return rec

    def sample_replay_groups(self, rng, count):
        import numpy as np
        scores=[]
        for positions,_ in self.groups:
            p=self.mask[np.asarray(positions,dtype=np.int64)]
            # Prefer completed groups, protect even partially solved groups.
            scores.append(float(p.sum()) + 3*int(p[:3].all()) +
                          3*int(p[3:].all()) + 6*int(p.all()))
        weight=np.asarray(scores,dtype=np.float64)
        if not weight.any():
            raise ValueError('V9_NO_REPLAYABLE_MATHEMATICAL_GROUPS')
        weight /= weight.sum()
        return rng.choice(len(self.groups), size=count, replace=True, p=weight)

    def as_csv(self):
        result=[]
        for i,row in enumerate(self.rows):
            result.append({'id':row['id'],'group':row['group'],
                           'surface':row['surface'],
                           'prompt_sha256':row['prompt_sha256'],
                           'target':str(row['target']),
                           'correct_at_initial':int(self.acquired_step[i]==0),
                           'protected':int(self.mask[i]),
                           'first_correct_step':int(self.acquired_step[i])})
        return result


def _strict_update(torch, net, optimizer, x, old, y, anchor,
                   *, groups_selected, net_spec, mu, sigma, device,
                   ledger, max_halvings, replay_weight):
    """Perform one gradient update; interpolate/rollback if any ledger case fails.

    Adam moment state represents this gradient independent of the selected
    step-size multiplier. If no safe update exists, restore both parameters
    and moments. No DEV or TEST is accessed during admission.
    """
    import numpy as np
    from torch.nn import functional as F
    before = _copy_state(net)
    optimizer_before = copy.deepcopy(optimizer.state_dict())
    local_triples=[(6*k+j*3,6*k+j*3+1,6*k+j*3+2)
                   for k in range(groups_selected) for j in range(2)]
    net.train();optimizer.zero_grad(set_to_none=True)
    base_loss, parts = joint._training_loss(torch, net, x, old, y,
                    torch.zeros(len(y),device=device,dtype=torch.bool),
                    weight=1.,source=False)
    triple=joint._triple_loss(torch,net,x,old,local_triples)
    if bool(anchor.any()):
        residual=net(x)
        logits=joint._head_logits(torch,old,residual)
        labels=torch.sign(y).long()+1
        truth=logits.gather(1,labels[:,None]).squeeze(1)
        other=logits.clone().scatter(1,labels[:,None],-1e6).amax(1)
        # Per-answer margin, including exact zeros and nonzero reversals.
        anchor_loss=F.relu(1.0 - (truth-other))[anchor].square().mean()
    else:
        anchor_loss=base_loss.new_zeros(())
    loss=base_loss + 2*triple + replay_weight*anchor_loss
    if not torch.isfinite(loss):
        raise ValueError('V9_NONFINITE_TRAIN_LOSS')
    loss.backward()
    torch.nn.utils.clip_grad_norm_(net.parameters(),1.0)
    optimizer.step()
    proposed=_copy_state(net)
    acceptance=None
    # Every candidate is tested on ALL TRAIN prompts through protected policy.
    # No numerical error tolerance, aggregate percentage or random subset.
    for h in range(max_halvings+1):
        alpha=2.0**(-h)
        with torch.no_grad():
            for key,param in net.state_dict().items():
                if not torch.is_floating_point(param):
                    if not torch.equal(before[key],proposed[key]):
                        raise ValueError('V9_UNEXPECTED_NONFLOAT_STATE_CHANGE')
                    continue
                param.copy_(before[key].to(param.device) +
                            alpha*(proposed[key].to(param.device)-before[key].to(param.device)))
        correct=_correct_train(torch,net,net_spec,mu,sigma,device)
        if ledger.accepts(correct):
            acceptance=(alpha,correct)
            break
    if acceptance is None:
        net.load_state_dict(before,strict=True)
        optimizer.load_state_dict(optimizer_before)
        return {'accepted':False,'alpha':0.,'loss':float(loss.detach()),
                'anchor_loss':float(anchor_loss.detach()),
                'new_correct':0,**_memory_summary(ledger.mask,ledger.groups)}
    return {'accepted':True,'alpha':float(acceptance[0]),
            'loss':float(loss.detach()),
            'anchor_loss':float(anchor_loss.detach()),
            '_correct':acceptance[1]}


def _fit_replay(torch, spec, initial, mu, sigma, *, device, steps,
                eval_every, seed, lr, replay_weight, max_halvings):
    import numpy as np
    groups, train_hash = _groups_and_fingerprints(spec)
    tx,told,ty,rows,routes=spec['train_synthetic']
    sx,sold,_,sroute=spec['train_source']
    torch.manual_seed(seed)
    net=v6._build_aligned_model(torch,protected=True).to(device)
    net.load_state_dict(initial,strict=True)
    rng=np.random.default_rng(seed)
    ledger=MathematicalLedger(_correct_train(torch,net,spec,mu,sigma,device),rows,groups)
    initial_mask=ledger.mask.copy()
    data=v6._encode(tx,told,routes,mu,sigma)
    x_tensor=torch.as_tensor(data,device=device,dtype=torch.float32)
    old_tensor=torch.as_tensor(told,device=device,dtype=torch.float32)
    y_tensor=torch.as_tensor(ty,device=device,dtype=torch.float32)
    optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=1e-4)
    def assess(step):
        retention, dev=v6._evaluate(torch,net,spec,mu,sigma,device)
        rank=joint._rank(retention,dev)
        return retention,dev,rank
    retain,dev,rank=assess(0)
    if retain['status']!='PASS':
        raise ValueError('V9_ORIGINAL_RETENTION_FAILED_AT_BASELINE')
    best={'step':0,'rank':rank,'retention':retain,'dev':dev,
          'weights':_copy_state(net),'ledger_mask':ledger.mask.copy(),
          'acquired_steps':ledger.acquired_step.copy()}
    hist=[{'step':0,'accepted':True,'alpha':0.,'loss':None,
           'dev_sign':dev['all']['balanced_sign_accuracy'],
           'dev_six':dev['cross_format']['complete_six_case_groups'],
           'retention':retain['status'],**ledger.events[0]}]
    accepted=0;rejected=0;backtracked=0
    for step in range(1,steps+1):
        chosen_new=rng.choice(len(groups),size=2,replace=True)
        chosen_memory=ledger.sample_replay_groups(rng,2)
        chosen=np.concatenate([chosen_new,chosen_memory])
        indices=np.concatenate([groups[int(i)][0] for i in chosen])
        protected=torch.as_tensor(ledger.mask[indices],device=device,dtype=torch.bool)
        proposal=_strict_update(torch,net,optimizer,x_tensor[indices],old_tensor[indices],
                y_tensor[indices],protected,groups_selected=len(chosen),
                net_spec=spec,mu=mu,sigma=sigma,device=device,ledger=ledger,
                max_halvings=max_halvings,replay_weight=replay_weight)
        if proposal['accepted']:
            accepted+=1
            if proposal['alpha']<1.:backtracked+=1
            acquisition=ledger.acquire(proposal.pop('_correct'),step)
        else:
            rejected+=1
            acquisition={'new_correct':0,**_memory_summary(ledger.mask,groups)}
        if step%eval_every==0 or step==steps:
            if not ledger.accepts(_correct_train(torch,net,spec,mu,sigma,device)):
                raise ValueError('V9_TRAIN_RETENTION_BROKEN_AT_EVALUATION')
            retain,dev,rank=assess(step)
            if retain['status']!='PASS':
                raise ValueError('V9_PROTECTED_ORIGINAL_RETENTION_BROKEN')
            hist.append({'step':step,'accepted':proposal['accepted'],
                'alpha':proposal['alpha'],'loss':proposal['loss'],
                'dev_sign':dev['all']['balanced_sign_accuracy'],
                'dev_zero_recall':dev['all']['zero_recall'],
                'dev_six':dev['cross_format']['complete_six_case_groups'],
                'retention':retain['status'], 'updates_accepted':accepted,
                'updates_rejected':rejected,'updates_backtracked':backtracked,
                **acquisition})
            if rank>best['rank']:
                best={'step':step,'rank':rank,'retention':retain,'dev':dev,
                      'weights':_copy_state(net),'ledger_mask':ledger.mask.copy(),
                      'acquired_steps':ledger.acquired_step.copy()}
            print('V9_REPLAY_PROGRESS '+probe.canonical({
                'step':step,'accepted':accepted,'rejected':rejected,
                'train_locked':int(ledger.mask.sum()),
                'dev_sign':dev['all']['balanced_sign_accuracy'],
                'dev_six':dev['cross_format']['complete_six_case_groups']}),flush=True)
    # Check selected weights against the historical memory AT ITS OWN STEP.
    net.load_state_dict(best['weights']);net.eval()
    if not bool(np.all(_correct_train(torch,net,spec,mu,sigma,device)[best['ledger_mask']])):
        raise ValueError('V9_SELECTED_CHECKPOINT_LOST_PROTECTED_MATHEMATICS')
    original_sign, original_value=v6._predict(torch,net,sx,sold,sroute,mu,sigma,device)
    if not (np.array_equal(original_value,np.asarray(sold,dtype=np.float64)) and
            np.array_equal(original_sign,lab._classify_continuous(sold))):
        raise ValueError('V9_ORIGINAL_TRAIN_BYPASS_CHANGED')
    final_full_run=_memory_summary(ledger.mask,groups)
    ledger.mask=best['ledger_mask'].copy()
    ledger.acquired_step=best['acquired_steps'].copy()
    return net,best,hist,ledger,{'full_run_final':final_full_run,
        'selected_checkpoint_step':best['step'], 'initial':_memory_summary(initial_mask,groups),
        'selected':_memory_summary(ledger.mask,groups),
        'initial_correct_protected_at_selected':int(np.sum(initial_mask)),
        'train_capture_sha256':train_hash,
        'updates_accepted':accepted,'updates_rejected':rejected,
        'updates_backtracked':backtracked,
        'replay_group_units':2, 'new_group_units':2,
        'per_update_full_train_hard_gate':True,
        'hard_retention_metric':'PROTECTED_PREDICTED_SIGN_CLASS_NOT_EXACT_NUMERIC_VALUE',
        'train_only_memory':True, 'all_acquired_correct_retained_at_selected':True,
        'selection_split':'DEV_ONLY','synthetic_test_use':'DIAGNOSTIC_ONLY'}


def _case_transition(ref, newer):
    prev={r['id']:r for r in ref}
    current={r['id']:r for r in newer}
    if prev.keys()!=current.keys():
        raise ValueError('V9_UNMATCHED_CASE_ATLAS')
    counts=Counter()
    for key in prev:
        a,b=prev[key],current[key]
        if any(a[k]!=b[k] for k in ('prompt_sha256','target_fraction','group',
               'surface','correction_admitted')):
            raise ValueError('V9_CHANGED_CASE_PROVENANCE')
        status=('retained_correct' if a['correct'] and b['correct'] else
                'lost_correct' if a['correct'] else
                'newly_correct' if b['correct'] else 'still_wrong')
        counts[status]+=1
    return {k:counts[k] for k in ('retained_correct','lost_correct',
                                'newly_correct','still_wrong')}


def _write_csv(path, rows):
    if not rows:raise ValueError('V9_EMPTY_CSV')
    with Path(path).open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(dict.fromkeys(
            key for row in rows for key in row)))
        writer.writeheader();writer.writerows(rows)


def _validate(args):
    for key in ('run','expanded_run','input','source_features','prior','output'):
        if getattr(args,key,None) is None:
            raise ValueError('V9_MISSING_ARGUMENT: '+key)
    if (args.steps<1 or args.steps>20000 or args.eval_every<1 or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.replay_weight) or args.replay_weight<0 or
        args.max_halvings<0 or args.max_halvings>12):
        raise ValueError('V9_INVALID_CONFIGURATION')


def run(args):
    import numpy as np
    import torch
    _validate(args)
    if args.device.startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('V9_CUDA_UNAVAILABLE')
    out=lab._output(args.output,[Path(x) for x in
          (args.run,args.expanded_run,args.input,args.source_features,args.prior)])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    net,mu,sd,cp=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    net=net.to(args.device)
    initial=_copy_state(net)
    groups,train_hash=_groups_and_fingerprints(spec)
    start_correct=_correct_train(torch,net,spec,mu,sd,args.device)
    initial_train=_memory_summary(start_correct,groups)
    ref_r,ref_d=v6._evaluate(torch,net,spec,mu,sd,args.device)
    if ref_r['status']!='PASS':raise ValueError('V9_V6_BASELINE_RETENTION_FAILED')
    cases_ref={split:v7._case_atlas(torch,net,spec,mu,sd,args.device,split)
               for split in ('train','dev','test')}
    control_net,control_best,control_history,control_meta=v7._fit_continuation(
        torch,spec,initial,mu,sd,focused=False,targeted_loss=False,
        device=args.device,steps=args.steps,eval_every=args.eval_every,
        seed=args.seed,lr=args.lr,zero_weight=0.,reversal_weight=0.)
    replay_net,replay_best,replay_history,ledger,replay_meta=_fit_replay(
        torch,spec,initial,mu,sd,device=args.device,steps=args.steps,
        eval_every=args.eval_every,seed=args.seed,lr=args.lr,
        replay_weight=args.replay_weight,max_halvings=args.max_halvings)
    # Recompute all comparisons once, after fitting; TEST enters only here.
    evaluated=[]
    states={'v6_reference':initial,'uniform_continuation':_copy_state(control_net),
            'monotonic_retention_replay':_copy_state(replay_net)}
    checkpoint_stats={'v6_reference':{'step':cp['selected_step'],
                           'retention':ref_r,'dev':ref_d},
        'uniform_continuation':{'step':control_best['step'],
                           'retention':control_best['retention'],
                           'dev':control_best['dev']},
        'monotonic_retention_replay':{'step':replay_best['step'],
                           'retention':replay_best['retention'],
                           'dev':replay_best['dev']}}
    models={'v6_reference':net,'uniform_continuation':control_net,
            'monotonic_retention_replay':replay_net}
    atlases={}
    for name,model in models.items():
        model.load_state_dict(states[name]);model.to(args.device)
        if not model.protected:raise ValueError('V9_PROTECTION_UNEXPECTEDLY_DISABLED')
        rows={split:v7._case_atlas(torch,model,spec,mu,sd,args.device,split)
              for split in ('train','dev','test')}
        atlases[name]=rows
        original_retention,dev=v6._evaluate(torch,model,spec,mu,sd,args.device)
        if original_retention['status']!='PASS':
            raise ValueError('V9_ORIGINAL_DEV_RETENTION_NOT_PASS: '+name)
        _,test=v6._evaluate(torch,model,spec,mu,sd,args.device,'test_synthetic')
        evaluated.append({'id':name,'selected_step':checkpoint_stats[name]['step'],
          'original_dev_retention':original_retention,
          'dev':dev,'previously_seen_test_diagnostic_only':test,
          'train':_memory_summary(np.asarray([r['correct'] for r in rows['train']],dtype=bool),groups),
          'promotion_authorized':False})
    for split in ('train','dev','test'):
        if _case_transition(cases_ref[split],atlases['v6_reference'][split])['lost_correct']:
            raise ValueError('V9_REFERENCE_PREDICTIONS_CHANGED')
    control_train_mask=np.asarray([r['correct'] for r in atlases['uniform_continuation']['train']],dtype=bool)
    replay_train_mask=np.asarray([r['correct'] for r in atlases['monotonic_retention_replay']['train']],dtype=bool)
    if not np.all(replay_train_mask[start_correct]):
        raise ValueError('V9_SELECTED_REPLAY_LOST_V6_TRAIN_CORRECT')
    joint._verify_after(data)
    # Selection is restricted to passing original retention and the complete
    # currently acquired mathematical TRAIN ledger. Uniform arm cannot claim
    # it satisfies a replay ledger merely by having higher DEV rank.
    winner=max((evaluated[0],evaluated[2]),key=lambda r:joint._rank(
        r['original_dev_retention'],r['dev']))
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'selected_candidate':winner['id'],'candidates':evaluated,
        'v6_reference_selected_step':cp['selected_step'],
        'v6_reference_verified_arm':v7.REQUIRED_PRIOR,
        'source_lineage':data['probe_info']['lineage'],
        'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
        'original_train_cache_sha256':data['train_cache']['sha256'],
        'train_capture_sha256':train_hash,
        'uniform_control_exact_v7_training_function':True,
        'training_ablation':{'start_weights_identical':True,
            'same_training_examples_and_global_seed':True,
            'same_4_complete_group_units_per_gradient_step':True,
            'difference':'REPLAY_SAMPLING_MARGIN_AND_HARD_RETENTION_BACKTRACK',
            'replay_memory':'TRAIN_ONLY_MONOTONIC_ACQUISITION',
            'checkpoint_selection':'DEV_ONLY_WITH_FULL_TRAIN_RETENTION_GATE',
            'previously_seen_test':'DIAGNOSTIC_ONLY_NOT_VIRGIN'},
        'mathematical_retention':replay_meta,
        'replay_candidate_saved_even_if_not_selected':True,
        'initial_train_competence':initial_train,
        'transitions':{('previously_seen_test_diagnostic_only' if split=='test' else split):
            {arm:_case_transition(cases_ref[split],atlases[arm][split])
            for arm in ARMS} for split in ('train','dev','test')},
        'checkpoint_history':{'uniform_continuation':control_history,
                              'monotonic_retention_replay':replay_history},
        'original_task_protection':'EXACT_V6_CAPTURE_BOUND_HARD_BYPASS_UNCHANGED',
        'general_production_router':'NOT_CERTIFIED',
        'qwen_frozen':True,'lora_frozen':True,'production_head_frozen':True,
        'production_weights_modified':False,'synthetic_dev_used_for_fitting':False,
        'original_dev_used_for_fitting':False,'synthetic_test_used_for_fitting_or_selection':False,
        'promotion_authorized':False,
        'config':{'steps':args.steps,'eval_every':args.eval_every,'lr':args.lr,
                  'seed':args.seed,'replay_weight':args.replay_weight,
                  'max_halvings':args.max_halvings}}
    out.mkdir(parents=True,exist_ok=False)
    _write_csv(out/'mathematical_train_retention_ledger.csv',ledger.as_csv())
    _write_csv(out/'mathematical_acquisition_events.csv',ledger.events)
    for name,arm_rows in atlases.items():
        for split,entries in arm_rows.items():
            label='previously_seen_test' if split=='test' else split
            _write_csv(out/f'{name}_cases_{label}.csv',entries)
            _write_csv(out/f'{name}_groups_{label}.csv',v7._group_atlas(entries))
    for name in ('uniform_continuation','monotonic_retention_replay'):
        _write_csv(out/f'{name}_checkpoint_history.csv',
                   control_history if name=='uniform_continuation' else replay_history)
    _write_csv(out/'candidate_comparison.csv',[
        {'candidate':x['id'],'step':x['selected_step'],
         'dev_sign':x['dev']['all']['balanced_sign_accuracy'],
         'dev_six':x['dev']['cross_format']['complete_six_case_groups'],
         'dev_zero_recall':x['dev']['all']['zero_recall'],
         'previously_seen_test_sign_diagnostic_only':x['previously_seen_test_diagnostic_only']['all']['balanced_sign_accuracy'],
         'previously_seen_test_six_diagnostic_only':x['previously_seen_test_diagnostic_only']['cross_format']['complete_six_case_groups'],
         'train_correct':x['train']['correct_cases'],
         'train_triples':x['train']['complete_triples'],
         'train_six':x['train']['complete_six_case_groups'],
         'original_retention':x['original_dev_retention']['status']}
        for x in evaluated])
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                     allow_nan=False)+'\n',encoding='utf-8')
    # Persist mathematical TRAIN-only commitments with the replay checkpoint.
    # Recomputing a step-zero ledger later would discard acquisition history.
    prior_sha=hashlib.sha256(Path(args.prior).read_bytes()).hexdigest()
    replay_ledger={'source_split':'SYNTHETIC_TRAIN_ONLY',
        'train_capture_sha256':train_hash,
        'rows':ledger.as_csv(),
        'reference_correct_cases':initial_train['correct_cases'],
        'selected_step':replay_best['step'],
        'hard_retain_all_acquired_correct':True}
    def checkpoint(name,weights,step,*,with_ledger):
        payload={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
            'arm':name,'weights':weights,'mean':torch.as_tensor(mu),
            'std':torch.as_tensor(sd),'prior_v6':str(Path(args.prior).resolve()),
            'prior_v6_sha256':prior_sha,'frozen_lineage':data['probe_info']['lineage'],
            'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
            'original_train_cache_sha256':data['train_cache']['sha256'],
            'train_capture_sha256':train_hash,
            'protected_original_passthrough':True,
            'capture_bound_router_only':True,'promotion_authorized':False,
            'selected_step':step}
        if with_ledger:
            payload['mathematical_train_retention_ledger']=replay_ledger
        return payload
    torch.save(checkpoint('monotonic_retention_replay',
        states['monotonic_retention_replay'],replay_best['step'],with_ledger=True),
        out/'monotonic_retention_replay_candidate.pt')
    torch.save(checkpoint(winner['id'],states[winner['id']],winner['selected_step'],
        with_ledger=(winner['id']=='monotonic_retention_replay')),
        out/'disposable_candidate.pt')
    for r in evaluated:
        print('V9_RESULT '+probe.canonical({'architecture':r['id'],
            'step':r['selected_step'],
            'dev_sign':r['dev']['all']['balanced_sign_accuracy'],
            'dev_six':r['dev']['cross_format']['complete_six_case_groups'],
            'test_sign_diagnostic_only':r['previously_seen_test_diagnostic_only']['all']['balanced_sign_accuracy'],
            'test_six_diagnostic_only':r['previously_seen_test_diagnostic_only']['cross_format']['complete_six_case_groups'],
            'train_correct':r['train']['correct_cases'],
            'retention':r['original_dev_retention']['status']}),flush=True)
    print('V9_COMPLETE '+probe.canonical({'status':report['status'],
        'selected':winner['id'],'initial_train':initial_train,
        'selected_replay_train':replay_meta['selected'],
        'accepted_steps':replay_meta['updates_accepted'],
        'rejected_steps':replay_meta['updates_rejected'],
        'promotion':False,'report':str(out/'report.json')}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--prior',type=Path,help='Exact selected V6 disposable_candidate.pt')
    p.add_argument('--output',type=Path)
    p.add_argument('--device',default='cuda')
    p.add_argument('--steps',type=int,default=300)
    p.add_argument('--eval-every',type=int,default=30)
    p.add_argument('--lr',type=float,default=.001)
    p.add_argument('--seed',type=int,default=49)
    p.add_argument('--replay-weight',type=float,default=2.)
    p.add_argument('--max-halvings',type=int,default=7)
    return p


def main(argv=None):
    run(parser().parse_args(argv))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
