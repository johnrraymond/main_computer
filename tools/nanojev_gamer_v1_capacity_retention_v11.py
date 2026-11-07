#!/usr/bin/env python3
"""V11: isolate recurring TRAIN conflicts; compare projector control with added capacity.

Every arm starts at the same selected protected V6 function. The control
replays V10 projected updates. The expanded arm freezes that V6 specialist and
trains a second, independently parameterized, route-specific residual module,
subject to EXACTLY the same full-TRAIN acquisition/retention gate and V6
bounded inference. Selected and terminal weights/ledgers are both saved.

DEV may select eligible checkpoints; repeatedly examined TEST is diagnostic
only and is evaluated after selection. No production router or promotion.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_math_transition_v8 as v8
from tools import nanojev_gamer_v1_protected_math_repair_v7 as v7
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_head_only_lab as lab
from tools import nanojev_gamer_v1_sign_information_probe as probe

SCHEMA = 'nanojev.gamer.v1.capacity-retention.v11'
ARMS = ('projected_control', 'expanded_capacity_projected')


def _expanded_model(torch, width=64):
    """A separate zero-start specialist. Frozen V6 is immutable, not a teacher fit.

    Route/fingerprint admission is already certified in V6 input preparation.
    New module sees encoded frozen features, never labels or group IDs at inference.
    Total final correction is bounded by the existing V6 limits.
    """
    if not isinstance(width, int) or not 8 <= width <= 256:
        raise ValueError('V11_BAD_CAPACITY_WIDTH')
    class Expanded(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.base = v6._build_aligned_model(torch, protected=True)
            self.base.requires_grad_(False)
            self.native = torch.nn.Sequential(torch.nn.Linear(1024, width),
                                               torch.nn.LayerNorm(width), torch.nn.GELU())
            self.game = torch.nn.Sequential(torch.nn.Linear(1024, width),
                                             torch.nn.LayerNorm(width), torch.nn.GELU())
            self.decoder = torch.nn.Sequential(torch.nn.Linear(width+1, 128),
                                                torch.nn.GELU(), torch.nn.Linear(128, 4))
            torch.nn.init.zeros_(self.decoder[-1].weight)
            torch.nn.init.zeros_(self.decoder[-1].bias)
            self.protected = True
        def forward(self, x):
            if x.ndim != 2 or x.shape[1] != 1026:
                raise ValueError('V11_BAD_ENCODED_INPUT')
            with torch.no_grad():
                original = self.base(x)
            route = x[:, -2:-1]
            latent = torch.where(route > .5, self.game(x[:, :1024]),
                                 self.native(x[:, :1024]))
            proposed = self.decoder(torch.cat((latent, x[:, -1:]), dim=-1))
            limits = original.new_tensor([v6.MAX_SIGNED_LOG_CORRECTION] +
                                         [v6.MAX_CLASS_LOGIT_CORRECTION]*3)
            room = torch.clamp(limits - torch.abs(original), min=0.)
            # Exactly identical at initialization; never exceed V6 residual caps.
            corrected = original + room * torch.tanh(proposed)
            return corrected * (route >= 0).to(corrected.dtype)
    return Expanded()


def _expanded_initial(torch, v6_state, width, seed):
    torch.manual_seed(seed)
    model = _expanded_model(torch, width)
    model.base.load_state_dict(v6_state, strict=True)
    if not all(not p.requires_grad for p in model.base.parameters()):
        raise ValueError('V11_BASE_NOT_FROZEN')
    return v9._copy_state(model)


def _csv(path, rows):
    if not rows:
        raise ValueError('V11_EMPTY_OUTPUT: ' + str(path))
    v10._csv(path, rows)


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_v10(torch, path, data, prior_hash, capture_sha, spec):
    """Require existing V10 evidence from this exact input; never trust file names."""
    import numpy as np
    root = Path(path)
    source = root/'report.json'
    checkpoint = root/'conflict_projected_candidate.pt'
    conflict_file = root/'conflict_projected_conflict_cases.csv'
    if not all(p.is_file() for p in (source, checkpoint, conflict_file)):
        raise ValueError('V11_REQUIRED_V10_EVIDENCE_MISSING')
    report = json.loads(source.read_text(encoding='utf-8'))
    candidates = {c['arm']: c for c in report.get('candidates', [])}
    if (report.get('schema') != v10.SCHEMA or
        report.get('status') != 'EXPERIMENT_COMPLETE_NO_PROMOTION' or
        report.get('production_weights_modified') is not False or
        report.get('promotion_authorized') is not False or
        report.get('previously_seen_test_used_for_training_or_selection') is not False or
        report.get('source_dev_used_for_training') is not False or
        report.get('synthetic_dev_used_for_training') is not False or
        report.get('frozen_lineage') != data['probe_info']['lineage'] or
        report.get('v6_prior_sha256') != prior_hash or
        report.get('training_capture_sha256') != capture_sha or
        report.get('synthetic_dataset_sha256') != data['probe_info']['dataset_sha256'] or
        report.get('original_train_cache_sha256') != data['train_cache']['sha256'] or
        'conflict_projected' not in candidates):
        raise ValueError('V11_V10_PROVENANCE_MISMATCH')
    cp = torch.load(checkpoint, map_location='cpu', weights_only=True)
    if (cp.get('schema') != v10.SCHEMA or
        cp.get('status') != 'DISPOSABLE_NON_PRODUCTION' or
        cp.get('arm') != 'conflict_projected' or
        cp.get('prior_v6_sha256') != prior_hash or
        cp.get('train_capture_sha256') != capture_sha or
        cp.get('promotion_authorized') is not False or
        cp.get('protected_original_passthrough') is not True or
        cp.get('frozen_lineage') != data['probe_info']['lineage'] or
        cp.get('selected_step') != candidates['conflict_projected']['selected_step']):
        raise ValueError('V11_V10_CHECKPOINT_MISMATCH')
    memory = cp.get('mathematical_train_retention_ledger', {})
    if (memory.get('source_split') != 'SYNTHETIC_TRAIN_ONLY' or
        memory.get('train_capture_sha256') != capture_sha or
        memory.get('selected_step') != cp['selected_step']):
        raise ValueError('V11_V10_MEMORY_PROVENANCE_MISMATCH')
    rows = spec['train_synthetic'][3]
    expected = {r['id']: r for r in rows}
    recorded = memory.get('rows')
    if (not isinstance(recorded, list) or len(recorded) != len(rows) or
        len({r.get('id') for r in recorded}) != len(rows)):
        raise ValueError('V11_V10_LEDGER_INCOMPLETE')
    for rec in recorded:
        row = expected.get(rec['id'])
        if (row is None or rec['prompt_sha256'] != row['prompt_sha256'] or
            rec['group'] != row['group'] or
            str(rec['target']) != str(row['target'])):
            raise ValueError('V11_V10_LEDGER_CAPTURE_CHANGED')
    old_net = v6._build_aligned_model(torch, protected=True)
    old_net.load_state_dict(cp['weights'], strict=True)
    # Candidate is only an independently verified historic observation; no fitting.
    conflicts = []
    with conflict_file.open(newline='', encoding='utf-8') as stream:
        for record in csv.DictReader(stream):
            if record.get('stage') == 'NONE':
                continue
            case = expected.get(record.get('case_id'))
            if (case is None or case['group'] != record.get('group') or
                case['surface'] != record.get('surface') or
                case['prompt_sha256'] != record.get('prompt_sha256')):
                raise ValueError('V11_V10_CONFLICT_CASE_CAPTURE_CHANGED')
            conflicts.append(record)
    observed=Counter(r['group'] for r in conflicts if r['stage']=='raw_smallest')
    declared=[(str(g),int(n)) for g,n in candidates['conflict_projected']
              ['training_diagnostics']['top_conflicting_groups']]
    if observed.most_common(15) != declared:
        raise ValueError('V11_V10_CONFLICT_CENSUS_MISMATCH')
    return report, cp, old_net, conflicts, {
        'report_sha256': _hash(source), 'checkpoint_sha256': _hash(checkpoint),
        'conflicts_sha256': _hash(conflict_file),
        'historical_selected_step': int(cp['selected_step']),
        'historical_conflict_cases': len(conflicts)}


def _ready_for_inference(torch, model, device, label):
    """Move a restored CPU model onto the evaluation device, and verify it.

    Checkpoint deserialization deliberately uses map_location='cpu'.  All V11
    calls to V6._predict require model parameters and encoded inputs on the
    same device; inspect each model independently before running preflight.
    """
    target = torch.device(device)
    model = model.to(target)
    model.eval()
    actual = {parameter.device for parameter in model.parameters()}
    if not actual or any(
        observed.type != target.type or
        (target.index is not None and observed.index != target.index)
        for observed in actual
    ):
        raise ValueError('V11_INFERENCE_DEVICE_MISMATCH_' + label)
    return model


def _ledger_csv(rows, mask, acquired):
    result=[]
    for i,row in enumerate(rows):
        result.append({'id':row['id'],'group':row['group'],
            'surface':row['surface'],'prompt_sha256':row['prompt_sha256'],
            'target':str(row['target']), 'protected':int(mask[i]),
            'first_correct_step':int(acquired[i]),
            'correct_at_initial':int(acquired[i]==0)})
    return result


def _evaluate_snapshot(torch, net, snapshot, spec, mu, sd, device, groups):
    import numpy as np
    net.load_state_dict(snapshot['weights'], strict=True)
    correct=v9._correct_train(torch,net,spec,mu,sd,device)
    if (not np.array_equal(correct, snapshot['ledger_mask']) or
        not np.array_equal(snapshot['ledger_mask'],snapshot['acquired_step']>=0)):
        raise ValueError('V11_CHECKPOINT_LEDGER_NOT_ACTUAL_PREDICTIONS')
    ret, dev=v6._evaluate(torch,net,spec,mu,sd,device)
    if ret['status']!='PASS':
        raise ValueError('V11_ORIGINAL_TASK_RETENTION_FAILURE')
    sx,sold,_,sroutes=spec['train_source']
    original_cls,original_val=v6._predict(torch,net,sx,sold,sroutes,mu,sd,device)
    if (not np.array_equal(original_val,np.asarray(sold,dtype=np.float64)) or
        not np.array_equal(original_cls,lab._classify_continuous(sold))):
        raise ValueError('V11_FROZEN_ORIGINAL_OUTPUT_CHANGED')
    # Ensure the snapshot metadata was recorded at the actual trained step.
    if (dev['all']['balanced_sign_accuracy'] != snapshot['dev']['all']['balanced_sign_accuracy'] or
        dev['cross_format']['complete_six_case_groups'] !=
        snapshot['dev']['cross_format']['complete_six_case_groups']):
        raise ValueError('V11_SNAPSHOT_DEV_CHANGED')
    return {'original_retention':ret,'dev':dev,'train':v9._memory_summary(correct,groups)}


def _dev_transitions(reference, candidate, arm, state):
    ra={r['id']:r for r in reference}
    ca={r['id']:r for r in candidate}
    v8._verify_alignment(ra,ca)
    rg=v8._group_index(ra); cg=v8._group_index(ca)
    case=[]; group=[]
    for rid, a in ra.items():
        b=ca[rid]
        case.append({'arm':arm,'state':state,'case_id':rid,'group':a['group'],
           'surface':a['surface'],'target_sign':a['target_sign'],
           'reference_sign':a['predicted_sign'],'candidate_sign':b['predicted_sign'],
           'reference_correct':a['correct'],'candidate_correct':b['correct'],
           'transition':v8._transition(a,b),'target_fraction':a['target_fraction'],
           'operand_band':a['operand_band'],'magnitude_band':a['magnitude_band']})
    for gid,a in rg.items():
        b=cg[gid]
        group.append({'arm':arm,'state':state,'group':gid,
            'reference_complete':a['complete'],'candidate_complete':b['complete'],
            'reference_correct_cases':a['correct_count'],
            'candidate_correct_cases':b['correct_count'],
            'new_complete':int(not a['complete'] and b['complete']),
            'lost_complete':int(a['complete'] and not b['complete']),
            'reference_triples':sum(a['triples'].values()),
            'candidate_triples':sum(b['triples'].values())})
    return case, group


def _critical_groups(torch, prior_net, original_net, states, spec, mu, sd, device,
                     conflicts, *, top=3):
    """Conflict frequency and per-member margin evolution, TRAIN exclusively."""
    import numpy as np
    full=spec['train_synthetic'][3]
    by_id={r['id']:i for i,r in enumerate(full)}
    small=[r for r in conflicts if r['stage']=='raw_smallest']
    counts=Counter(r['group'] for r in small)
    if not counts:
        # Short CPU smoke runs can accept every proposal. In actual V10, this
        # census identifies concrete hard conflicts; do not invent any.
        return [], [{'group':'NONE','case_id':'NONE',
                     'model_state':'NONE','note':'NO_V10_RAW_SMALLEST_CONFLICTS'}], counts
    chosen=[g for g,n in counts.most_common(top)]
    ix=[i for i,r in enumerate(full) if r['group'] in chosen]
    if len(ix)!=6*len(chosen):
        raise ValueError('V11_CRITICAL_GROUP_CAPTURE_INCOMPLETE')
    detail=Counter(r['case_id'] for r in small)
    trial_margin={}
    for row in small:
        if row.get('trial_true_class_margin'):
            trial_margin.setdefault(row['case_id'],[]).append(float(row['trial_true_class_margin']))
    snapshots={'v6_reference':(prior_net,None),'v10_projected_selected':(original_net,None)}
    for name,net,weights in states:
        snapshots[name]=(net,weights)
    per_state={}
    for name,(net,weights) in snapshots.items():
        if weights is not None:
            net.load_state_dict(weights,strict=True)
        per_state[name]=v10._case_margins(torch,net,spec,mu,sd,device,ix)
    records=[]
    for i in ix:
        r=full[i]
        for name,measures in per_state.items():
            value=measures[i]
            records.append({'group':r['group'],'case_id':r['id'],
                'prompt_sha256':r['prompt_sha256'],'surface':r['surface'],
                'target':r['target'],'target_sign':v7._signed(v10.Fraction(str(r['target']))),
                'model_state':name,'prediction':value['prediction'],
                'true_class_margin':value['true_class_margin'],
                'residual_signed_log':value['residual_signed_log'],
                'residual_logit_l2':value['residual_logit_l2'],
                'v10_smallest_rejection_events':detail[r['id']],
                'v10_smallest_trial_margin_min':min(trial_margin[r['id']])
                if trial_margin.get(r['id']) else None,
                'v10_smallest_trial_margin_max':max(trial_margin[r['id']])
                if trial_margin.get(r['id']) else None})
    return chosen,records,counts


def _validate(args):
    for name in ('run','expanded_run','input','source_features','prior','v10_run','output'):
        if getattr(args,name,None) is None:
            raise ValueError('V11_REQUIRED_ARGUMENT_MISSING_'+name)
    if (not 1<=args.steps<=20000 or not 1<=args.eval_every or
        not math.isfinite(args.lr) or args.lr<=0 or
        not math.isfinite(args.replay_weight) or args.replay_weight<0 or
        not 0<=args.max_halvings<=12 or not 1<=args.projection_passes<=20 or
        not 1<=args.max_constraints<=128 or not 8<=args.capacity_width<=256):
        raise ValueError('V11_BAD_CONFIGURATION')


def run(args):
    import numpy as np
    import torch
    _validate(args)
    if str(args.device).startswith('cuda') and not torch.cuda.is_available():
        raise RuntimeError('V11_CUDA_UNAVAILABLE')
    out=lab._output(args.output,[Path(getattr(args,n)) for n in
       ('run','expanded_run','input','source_features','prior','v10_run')])
    data,prepared=joint._prepare(args)
    spec,_=v6._routed_profiles(data,prepared)
    base,mu,sd,_=v7._read_v6_checkpoint(torch,args.prior,data,spec)
    starting=v9._copy_state(base)
    # V7 reads the V6 checkpoint onto CPU. Move it before the DEV atlas and
    # critical-group margin evaluations, which encode inputs on args.device.
    base=_ready_for_inference(torch,base,args.device,'V6_REFERENCE')
    groups,capture_sha=v9._groups_and_fingerprints(spec)
    prior_hash=_hash(args.prior)
    historic,cp,prior_v10,conflicts,prior_meta=_verify_v10(torch,args.v10_run,
                                                      data,prior_hash,capture_sha,spec)
    # V10 historical checkpoints are also deserialized on CPU. The first
    # TRAIN-ledger comparison is otherwise a CPU-weights/CUDA-input crash.
    prior_v10=_ready_for_inference(torch,prior_v10,args.device,'V10_HISTORICAL')
    old_memory={r['id']:int(r['protected']) for r in
                cp['mathematical_train_retention_ledger']['rows']}
    observed_old=v9._correct_train(torch,prior_v10,spec,mu,sd,args.device)
    if not np.array_equal(observed_old,np.asarray([old_memory[r['id']]
                            for r in spec['train_synthetic'][3]],dtype=bool)):
        raise ValueError('V11_HISTORICAL_V10_CHECKPOINT_LEDGER_MISMATCH')
    if historic['config']['steps']!=args.steps or historic['config']['eval_every']!=args.eval_every:
        raise ValueError('V11_V10_DIFFERENT_TRAINING_BUDGET')
    if historic['config']['lr']!=args.lr or historic['config']['seed']!=args.seed:
        raise ValueError('V11_V10_DIFFERENT_TRAINING_SCHEDULE')
    if (historic['config']['replay_weight']!=args.replay_weight or
        historic['config']['max_halvings']!=args.max_halvings or
        historic['config']['projection_passes']!=args.projection_passes or
        historic['config']['max_constraints']!=args.max_constraints):
        raise ValueError('V11_V10_DIFFERENT_RETENTION_RULES')
    baseline_dev=v7._case_atlas(torch,base,spec,mu,sd,args.device,'dev')
    assert len(baseline_dev)==len(spec['dev_synthetic'][3])
    starts={
        'projected_control':(starting,None,'conflict_projected'),
        'expanded_capacity_projected':(
            _expanded_initial(torch,starting,args.capacity_width,args.seed),
            lambda t:_expanded_model(t,args.capacity_width),'expanded_capacity_projected')}
    saved={}
    for arm,(initial,builder,underlying_arm) in starts.items():
        model,best,ledger,attempts,victims,metrics,stats=v10._fit_arm(
            torch,spec,initial,mu,sd,arm=underlying_arm,device=args.device,
            steps=args.steps,eval_every=args.eval_every,lr=args.lr,seed=args.seed,
            replay_weight=args.replay_weight,max_halvings=args.max_halvings,
            projection_passes=args.projection_passes,max_constraints=args.max_constraints,
            model_builder=builder,capture_terminal=True)
        terminal=stats.pop('terminal_snapshot')
        selected={'step':best['step'],'weights':best['weights'],
            'ledger_mask':best['ledger_mask'],'acquired_step':best['acquired_step'],
            'retention':best['retention'],'dev':best['dev'],'rank':best['rank']}
        saved[arm]={'model':model,'states':{'selected':selected,'terminal':terminal},
                    'attempts':attempts,'victims':victims,'history':metrics,
                    'diagnostics':stats}
        print('V11_ARM '+probe.canonical({'arm':arm,'selected_step':best['step'],
            'terminal_step':terminal['step'],
            'accepted':stats['accepted_updates'],
            'rescues':stats['projection_rescues'],
            'terminal_train':stats['full_run_final_train'],
            'selected_train':stats['selected_train']}),flush=True)
    comparisons=[]; case_changes=[]; group_changes=[]
    for arm,record in saved.items():
        model=record['model']
        for state,snapshot in record['states'].items():
            scores=_evaluate_snapshot(torch,model,snapshot,spec,mu,sd,args.device,groups)
            atlas=v7._case_atlas(torch,model,spec,mu,sd,args.device,'dev')
            case,group=_dev_transitions(baseline_dev,atlas,arm,state)
            case_changes.extend(case);group_changes.extend(group)
            results={'arm':arm,'state':state,'step':snapshot['step'],
                 'train_correct':scores['train']['correct_cases'],
                 'train_triples':scores['train']['complete_triples'],
                 'train_six':scores['train']['complete_six_case_groups'],
                 'dev_correct':scores['dev']['all']['correct_class'],
                 'dev_sign':scores['dev']['all']['balanced_sign_accuracy'],
                 'dev_zero_recall':scores['dev']['all']['zero_recall'],
                 'dev_triples':scores['dev']['all']['triple_all_correct'],
                 'dev_six':scores['dev']['cross_format']['complete_six_case_groups'],
                 'dev_gained_cases':sum(c['transition']=='newly_correct' for c in case),
                 'dev_lost_cases':sum(c['transition']=='lost_correct' for c in case),
                 'dev_gained_groups':sum(g['new_complete'] for g in group),
                 'dev_lost_groups':sum(g['lost_complete'] for g in group),
                 'original_retention':scores['original_retention']['status']}
            record['states'][state]['evaluation']=results
            comparisons.append(results)
    # Selector uses only original retention and DEV; terminal is independently
    # recorded as scientific evidence, not silently substituted for selected.
    winner=max((r for r in comparisons if r['state']=='selected'),
                key=lambda r:saved[r['arm']]['states']['selected']['rank'])
    critical_models=[]
    for arm,record in saved.items():
        for state,snap in record['states'].items():
            critical_models.append((arm+'_'+state,record['model'],snap['weights']))
    critical_names,critical_rows,critical_counts=_critical_groups(
        torch,base,prior_v10,critical_models,spec,mu,sd,args.device,conflicts)
    # TEST examination only after all trained states, DEV scoring and winner selection.
    for row in comparisons:
        record=saved[row['arm']]
        snapshot=record['states'][row['state']]
        record['model'].load_state_dict(snapshot['weights'],strict=True)
        _,test=v6._evaluate(torch,record['model'],spec,mu,sd,args.device,'test_synthetic')
        row['previously_seen_test_sign_diagnostic_only']=test['all']['balanced_sign_accuracy']
        row['previously_seen_test_six_diagnostic_only']=test['cross_format']['complete_six_case_groups']
    report={'schema':SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'frozen_lineage':data['probe_info']['lineage'],
       'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
       'original_train_cache_sha256':data['train_cache']['sha256'],
       'train_capture_sha256':capture_sha,'v6_prior_sha256':prior_hash,
       'v10_verified_prior':prior_meta,
       'historical_conflict_groups':critical_names,
       'historical_top_conflicts':critical_counts.most_common(10),
       'selected_arm':winner['arm'],'selected_step':winner['step'],
       'selection_policy':'EXISTING_JOINT_DEV_RANK_ORIGINAL_RETENTION_GATE',
       'comparisons':comparisons,
       'training_diagnostics':{k:v['diagnostics'] for k,v in saved.items()},
       'config':{'steps':args.steps,'eval_every':args.eval_every,
                 'lr':args.lr,'seed':args.seed,'replay_weight':args.replay_weight,
                 'max_halvings':args.max_halvings,
                 'projection_passes':args.projection_passes,
                 'max_constraints':args.max_constraints,
                 'capacity_width':args.capacity_width},
       'synthetic_train_only_retention_ledger':True,
       'source_dev_used_for_training':False,
       'synthetic_dev_used_for_training':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'capture_bound_original_task_bypass':'UNCHANGED_V6',
       'general_production_router':'NOT_CERTIFIED',
       'terminal_states_saved_separately':True,
       'selected_states_saved_separately':True,
       'production_weights_modified':False,'promotion_authorized':False}
    joint._verify_after(data)
    out.mkdir(parents=True,exist_ok=False)
    _csv(out/'comparison.csv',comparisons)
    _csv(out/'dev_case_transitions.csv',case_changes)
    _csv(out/'dev_group_transitions.csv',group_changes)
    _csv(out/'critical_train_group_margins.csv',critical_rows)
    _csv(out/'historical_conflict_cases.csv',conflicts or
         [{'stage':'NONE','case_id':'NONE','note':'NO_HISTORICAL_CONFLICTS'}])
    for arm,record in saved.items():
        _csv(out/(arm+'_attempts.csv'),record['attempts'])
        _csv(out/(arm+'_checkpoint_history.csv'),record['history'])
        if record['victims']:
            _csv(out/(arm+'_conflict_cases.csv'),record['victims'])
        for state,snapshot in record['states'].items():
            checked=record['model']
            checked.load_state_dict(snapshot['weights'],strict=True)
            if not np.array_equal(v9._correct_train(torch,checked,spec,mu,sd,args.device),
                                  snapshot['ledger_mask']):
                raise ValueError('V11_PRE_SAVE_LEDGER_MISMATCH')
            _csv(out/(f'{arm}_{state}_train_ledger.csv'),
                 _ledger_csv(spec['train_synthetic'][3],snapshot['ledger_mask'],
                             snapshot['acquired_step']))
            checkpoint={'schema':SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
                'arm':arm,'state':state,'step':snapshot['step'],
                'weights':snapshot['weights'],
                'mean':torch.as_tensor(mu),'std':torch.as_tensor(sd),
                'frozen_lineage':data['probe_info']['lineage'],
                'prior_v6_sha256':prior_hash,'v10_report_sha256':prior_meta['report_sha256'],
                'train_capture_sha256':capture_sha,
                'synthetic_dataset_sha256':data['probe_info']['dataset_sha256'],
                'original_train_cache_sha256':data['train_cache']['sha256'],
                'capacity_width':args.capacity_width if arm=='expanded_capacity_projected' else 0,
                'mathematical_train_retention_ledger':{
                  'source_split':'SYNTHETIC_TRAIN_ONLY',
                  'train_capture_sha256':capture_sha,'checkpoint_step':snapshot['step'],
                  'rows':_ledger_csv(spec['train_synthetic'][3],snapshot['ledger_mask'],
                                      snapshot['acquired_step']),
                  'correct_cases':int(snapshot['ledger_mask'].sum()),
                  'hard_retention_pass':True},
                'protected_original_passthrough':True,
                'capture_bound_router_only':True,
                'promotion_authorized':False}
            torch.save(checkpoint,out/(f'{arm}_{state}_candidate.pt'))
            if arm==winner['arm'] and state=='selected':
                torch.save(checkpoint,out/'disposable_candidate.pt')
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True,
                                          allow_nan=False)+'\n',encoding='utf-8')
    for rec in comparisons:
        print('V11_RESULT '+probe.canonical(rec),flush=True)
    print('V11_COMPLETE '+probe.canonical({'selected':winner['arm'],
         'selected_step':winner['step'], 'critical_groups':critical_names,
         'report':str(out/'report.json'),'promotion':False,
         'terminal_states_saved':True}),flush=True)
    return report


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('train',),default='train')
    p.add_argument('--run',type=Path)
    p.add_argument('--expanded-run',type=Path)
    p.add_argument('--input',type=Path)
    p.add_argument('--source-features',type=Path)
    p.add_argument('--prior',type=Path,help='Selected V6 protected unaligned checkpoint')
    p.add_argument('--v10-run',type=Path,help='Existing verified V10 run directory')
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
    p.add_argument('--capacity-width',type=int,default=64)
    return p


def main(argv=None):
    run(parser().parse_args(argv))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
