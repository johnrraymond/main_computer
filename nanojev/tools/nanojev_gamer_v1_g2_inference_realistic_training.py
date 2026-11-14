"""G2-0019: controlled TRAIN-only update of the G2-0016 relational scorer.

Train against the SAME label-blind top-24 competitor inventory used at inference.
Compare a matched distribution-only arm with a precision/recall objective arm.
The baseline G2-0016 scorer, G1, backbone, binding head and all prior runs stay
immutable. Existing exposed DEV is used for diagnosis, never checkpoint,
threshold, hyperparameter or promotion selection. This is not certification.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json
import math
import random

import torch
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_competitor_context_intervention as context18

SCHEMA = 'nanojev.gamer.graph.g2.inference-realistic-training.v1'
REPORT = 'g2_inference_realistic_training_report.json'
CASES = 'g2_inference_realistic_training_per_case.jsonl'
WEIGHTS = 'g2_inference_realistic_training_development.safetensors'
SEED = 460019
STEPS = 320
LR = 8.e-5
THRESHOLD = context18.THRESHOLD  # fixed 0.75: no sweep
TOP_K = 24  # exactly the G2-0016 selected inference inventory
GROUPS_PER_CASE = 8
ARMS = ('baseline_0016', 'matched_context', 'precision_guard')
SPLITS = ('calibration', *ceiling17.SPLITS)


def verify_parent_0018(directory, rows_by_split, hashes16, hashes17, parent17_records=None):
    """Fail closed on the complete frozen 0018 report and per-case ledger."""
    directory = Path(directory)
    rp, cp = directory/context18.REPORT, directory/context18.CASES
    report = json.loads(rp.read_text(encoding='utf-8'))
    required = {
        'schema': context18.SCHEMA,
        'status': 'G2_COMPETITOR_CONTEXT_INTERVENTION_DEV_NOT_CERTIFIED',
        'mode': 'FROZEN_PAIRED_CONTEXT_INTERVENTION',
        'parent_selected': ceiling17.GENERAL,
        'frozen_0016_hashes': hashes16,
        'parent_hashes': hashes17,
        'fixed_threshold': THRESHOLD,
        'new_trainable_parameters': 0,
        'candidate_gold_injection_disabled': True,
        'gold_used_only_for_offline_attribution': True,
        'no_hindsight_threshold_selection': True,
        'new_training': False,
        'model_selection': False,
        'sealed_test_used': False,
        'challenge_or_test_split_used': False,
        'production_promotion': False,
        'g2_production_unlocked': False,
        'certification_authorized': False,
    }
    for field, expected in required.items():
        if type(report.get(field)) is not type(expected) or report[field] != expected:
            raise ValueError('GAMER_G2_0019_PARENT_0018_CONTRACT:'+field)
    records = [json.loads(s) for s in cp.read_text(encoding='utf-8').splitlines() if s.strip()]
    if len(records) != sum(len(rows_by_split[s]) for s in ceiling17.SPLITS):
        raise ValueError('GAMER_G2_0019_PARENT_0018_COUNT')
    if parent17_records is None or set(parent17_records) != set(ceiling17.SPLITS):
        raise ValueError('GAMER_G2_0019_PARENT_0017_LEDGER_REQUIRED')
    offset = 0
    for group in ceiling17.SPLITS:
        rows = rows_by_split[group]
        chunk = records[offset:offset+len(rows)]
        offset += len(rows)
        if report.get('datasets_sha256', {}).get(group) != density.sha_rows(rows):
            raise ValueError('GAMER_G2_0019_PARENT_0018_DATASET_DRIFT')
        prior = parent17_records[group]
        if len(prior) != len(chunk):
            raise ValueError('GAMER_G2_0019_PARENT_0017_CASE_COUNT')
        for index, (rec, row, earlier) in enumerate(zip(chunk, rows, prior), 1):
            if (rec.get('group') != group or rec.get('index') != index
                    or rec.get('prompt_sha256') != base._sha(row['prompt'])
                    or rec.get('family') != row['family']
                    or rec.get('structural_topology') != row.get('structural_topology', 'native')):
                raise ValueError('GAMER_G2_0019_PARENT_0018_CASE_DRIFT')
            if (rec.get('configurations', {}).get('baseline_top24') !=
                        earlier['configurations'][ceiling17.GENERAL] or
                rec.get('candidate_inventory', {}).get('top24') !=
                        earlier['competition']['candidate_sum']):
                raise ValueError('GAMER_G2_0019_PARENT_0018_BASELINE_DRIFT')
        if context18.aggregate(chunk) != report.get('diagnosis', {}).get(group):
            raise ValueError('GAMER_G2_0019_PARENT_0018_METRIC_DRIFT')
        families={family:context18.aggregate([r for r in chunk if r['family']==family])
                  for family in ('encounter','allocation')
                  if any(r['family']==family for r in chunk)}
        density_groups={bucket:context18.aggregate([r for r in chunk if r['density_bucket']==bucket])
                        for bucket in sorted({r['density_bucket'] for r in chunk})}
        if families != report.get('by_family',{}).get(group):
            raise ValueError('GAMER_G2_0019_PARENT_0018_FAMILY_DRIFT')
        if density_groups != report.get('by_density',{}).get(group):
            raise ValueError('GAMER_G2_0019_PARENT_0018_DENSITY_DRIFT')
        if group == ceiling17.SPLITS[1]:
            topology={top:context18.aggregate([r for r in chunk if r['structural_topology']==top])
                      for top in ('2x4','4x2','4x4')}
            if topology != report.get('by_topology'):
                raise ValueError('GAMER_G2_0019_PARENT_0018_TOPOLOGY_DRIFT')
    return {'g2_0018_report_sha256': backbone.sha256(rp),
            'g2_0018_cases_sha256': backbone.sha256(cp)}


def label_blind_groups(inventory, *, seed, max_groups=GROUPS_PER_CASE):
    """Select COMPLETE inference groups using geometry only, never gold.

    Small/large pools are balanced so full-width competition is represented;
    candidate membership and rank within a group are NEVER gold-adjusted.
    """
    if type(seed) is not int or type(max_groups) is not int or max_groups < 1:
        raise ValueError('GAMER_G2_0019_BAD_GROUP_SELECTION')
    groups = inventory['groups']
    if (len(groups) != len(inventory['edges']) or not groups
            or any(groups[i] > groups[i+1] for i in range(len(groups)-1))):
        raise ValueError('GAMER_G2_0019_BAD_GROUP_INVENTORY')
    spans = []
    start = 0
    while start < len(groups):
        end = start+1
        while end < len(groups) and groups[end] == groups[start]:
            end += 1
        spans.append((groups[start], list(range(start,end))))
        start = end
    rng = random.Random(seed)
    full = [x for x in spans if len(x[1]) >= 16]
    smaller = [x for x in spans if len(x[1]) < 16]
    picked = rng.sample(full, min(len(full), (max_groups+1)//2))
    picked += rng.sample(smaller, min(len(smaller), max_groups-len(picked)))
    if len(picked) < max_groups:
        already = {g for g, _ in picked}
        remaining = [span for span in spans if span[0] not in already]
        picked += rng.sample(remaining, min(len(remaining), max_groups-len(picked)))
    return sorted({i for _, ids in picked for i in ids})


def training_objective(logits, targets, null, far, groups, *, arm):
    """Compare identical data/context with and without explicit FP pressure.

    Preserve independent Bernoulli multi-positive targets, never top-one softmax.
    Penalize confident false positives while keeping a differentiable positive
    recall term. The 0.75 decision boundary is frozen before training.
    """
    if (arm not in ('matched_context', 'precision_guard') or logits.ndim != 1
            or not logits.numel() or targets.shape != logits.shape
            or null.shape != logits.shape or far.shape != logits.shape
            or len(groups) != len(logits) or not torch.isfinite(logits).all()
            or not torch.isfinite(targets).all() or bool(((targets!=0)&(targets!=1)).any())):
        raise ValueError('GAMER_G2_0019_BAD_LOSS_INPUT')
    w = torch.where(targets>.5, 5.*(1.+null), 1.+.75*far)
    bce = (F.binary_cross_entropy_with_logits(logits, targets, reduction='none')*w).mean()
    rank = []
    members = defaultdict(list)
    for i, group in enumerate(groups): members[group].append(i)
    for indices in members.values():
        inds = torch.tensor(indices, device=logits.device)
        pos = logits[inds][targets[inds]>.5]
        neg = logits[inds][targets[inds]<.5]
        if pos.numel() and neg.numel():
            rank.append(F.softplus(.6-pos[:,None]+neg[None,:]).mean())
    loss = bce + (.5*torch.stack(rank).mean() if rank else 0.)
    if arm == 'precision_guard':
        cutoff = math.log(THRESHOLD/(1-THRESHOLD))
        wrong = logits[targets<.5]
        correct = logits[targets>.5]
        if wrong.numel():
            # Penalizes mistakes approaching/exceeding the fixed decision cutoff.
            loss = loss + .60*F.softplus(wrong-cutoff).mean()
        if correct.numel():
            # Does not sacrifice every positive in pursuit of lower FP counts.
            loss = loss + .40*F.softplus(cutoff-correct).mean()
    return loss


def train(cases, frozen, *, steps=STEPS, lr=LR, seed=SEED, device='cpu',
          inventory_fn=source13.make_inventory):
    """Train both matched arms on TRAIN-only prompt labels and predicted G1 nodes."""
    if (type(steps) is not int or steps < 1 or type(seed) is not int
            or not math.isfinite(lr) or lr <= 0 or not cases):
        raise ValueError('GAMER_G2_0019_BAD_TRAIN')
    if set(row['family'] for row in cases) != {'allocation', 'encounter'}:
        raise ValueError('GAMER_G2_0019_TRAIN_FAMILY_DIVERSITY')
    for row in cases:
        if (row.get('split') != 'train'
                or row.get('structural_topology') in ('4x4','4x2','2x4')
                or row.get('force_training_gold')):
            raise ValueError('GAMER_G2_0019_NONTRAIN_SUPERVISION')
    frozen.eval().requires_grad_(False)
    original = {k: v.detach().cpu().clone() for k,v in frozen.state_dict().items()}
    models = {arm:deepcopy(frozen).to(device).train().requires_grad_(True)
              for arm in ARMS[1:]}
    opts = {arm:torch.optim.AdamW(model.parameters(), lr=lr)
            for arm,model in models.items()}
    by_family = {name:[i for i,row in enumerate(cases) if row['family']==name]
                 for name in ('allocation','encounter')}
    rng = random.Random(seed)
    @lru_cache(maxsize=4)
    def inventory(i):
        row=cases[i]
        inv=inventory_fn(row, row['rank'], row['allowed'], top_k=TOP_K, train_gold=False)
        if len(inv['edges']) != len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0019_DUPLICATE_TRAIN_CANDIDATES')
        return inv
    counts=Counter();last={}
    for step in range(steps):
        batch=[]
        for j in range(3):
            index=rng.choice(by_family[('encounter','allocation')[(step+j)%2]])
            inv=inventory(index)
            if not inv['edges']:continue
            ids=label_blind_groups(inv,seed=seed+step*101+index*7+j)
            row=cases[index]
            x=inv['features'][ids].to(device)
            b=inv['base'][ids].to(device)
            gs=[inv['groups'][i] for i in ids]
            y=torch.tensor([float(inv['edges'][i] in row['gold']) for i in ids],device=device)
            null=torch.tensor([float(inv['null_argmax'][i]) for i in ids],device=device)
            far=torch.tensor([float(abs(inv['edges'][i][0][0]-inv['edges'][i][1][0])>128)
                              for i in ids],device=device)
            counts['train_samples']+=len(ids)
            counts['train_positives']+=int(y.sum())
            counts['train_negatives']+=int((1.-y).sum())
            counts['train_groups']+=len(set(gs))
            batch.append((x,b,gs,y,null,far))
        if not batch:raise ValueError('GAMER_G2_0019_EMPTY_TRAIN_BATCH')
        for arm, model in models.items():
            losses=[]
            for x,b,gs,y,null,far in batch:
                logits=model(x,b,gs,with_competition=True)
                losses.append(training_objective(logits,y,null,far,gs,arm=arm))
            opt=opts[arm]
            opt.zero_grad(set_to_none=True)
            loss=torch.stack(losses).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0019_NONFINITE_LOSS')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            opt.step()
            last[arm]=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0019_TRAIN {step+1}/{steps} '
                  f'matched={last["matched_context"]:.6f} guarded={last["precision_guard"]:.6f}',
                  flush=True)
    for model in models.values():model.eval().requires_grad_(False)
    if any(not torch.equal(frozen.state_dict()[k].detach().cpu(),v) for k,v in original.items()):
        raise ValueError('GAMER_G2_0019_FROZEN_BASELINE_CHANGED')
    return models, {'steps':steps,'lr':lr,'counts':dict(counts), 'last_loss':last,
                    'train_cases':len(cases),'candidate_top_k':TOP_K,
                    'gold_in_candidate_construction':False,
                    'label_blind_full_inference_groups':True,
                    'same_initial_weights_and_training_batches':True,
                    'multi_positive_supervision':True}


def evaluate_case(case, row, old, head, baseline, trained, allowed, *, group, index, device):
    if (base._sha(row['prompt']) != case['prompt_sha256']
            or old['prompt_sha256'] != case['prompt_sha256']
            or old['family'] != row['family'] or old['index'] != index
            or old['group'] != group):
        raise ValueError('GAMER_G2_0019_EVAL_IDENTITY_DRIFT')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        x,b=inv['features'].to(device),inv['base'].to(device)
        scores={arm:model(x,b,inv['groups'],with_competition=True).detach().cpu()
                for arm,model in [('baseline_0016',baseline),*trained.items()]}
        result={arm:sel.score_set(source13.decode_scores(inv,logits,THRESHOLD),case['gold'])
                for arm,logits in scores.items()}
        if result['baseline_0016'] != old['configurations'][ceiling17.GENERAL]:
            raise ValueError('GAMER_G2_0019_FROZEN_BASELINE_REPLAY_DRIFT')
    return {'group':group,'index':index,'prompt_sha256':case['prompt_sha256'],
            'family':case['family'], 'structural_topology':row.get('structural_topology','native'),
            'density_bucket':density._bucket(len(case['anchors'])),
            'candidate_count':len(inv['edges']),'gold_binding_edges':len(case['gold']),
            'configurations':result}


def aggregate(records):
    if not records: raise ValueError('GAMER_G2_0019_EMPTY_EVALUATION')
    return {'cases':len(records),
            'metrics':{arm:base.metrics([{**r['configurations'][arm],'family':r['family']}
                                         for r in records]) for arm in ARMS}}


def predeclared_gates(grouped):
    """Report (do not promote/select) improvement, FP and recall constraints."""
    result={}
    for group,rows in grouped.items():
        by_arm=aggregate(rows)['metrics']
        base_metric=by_arm['baseline_0016']
        result[group]={}
        for arm in ARMS[1:]:
            metric=by_arm[arm]
            result[group][arm]={
                'f1_above_baseline':metric['f1']>base_metric['f1'],
                'fewer_false_positives':metric['false_positive']<base_metric['false_positive'],
                'recall_not_below_baseline_minus_002':metric['recall']>=base_metric['recall']-.02,
                'exact_cases_above_baseline':metric.get('exact_cases',0)>base_metric.get('exact_cases',0),
            }
    return result


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,
        robustness_run,diagnosis_run,source_run,specialization_run,routing_robustness_run,
        general_run,ceiling_run,context18_run,output,device='cuda',max_length=2048,
        steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0019_IMMUTABLE_OUTPUT')
    if steps!=STEPS:raise ValueError('GAMER_G2_0019_FIXED_PROTOCOL_REQUIRED')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0019_PARENT_TRAIN_DRIFT')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0019_PARENT_BOOTSTRAP_DRIFT')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0019_PARENT_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,
        specialization_run=specialization_run)
    training=original+augmented
    report16,records16,hashes16=ceiling17.verify_frozen_0016(
        general_run,source_run,routing_robustness_run,augmented,training,
        calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256'] !=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256']!=parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0019_PARENT_0016_LINEAGE')
    rows_by_split=dict(zip(ceiling17.SPLITS,(independent,withheld)))
    records17,hashes17=context18.verify_parent_0017(ceiling_run,rows_by_split,hashes16)
    hashes18=verify_parent_0018(context18_run,rows_by_split,hashes16,hashes17,records17)
    all_rows=(*training,*calibration,*independent,*withheld)
    if len({base._sha(r['prompt']) for r in all_rows}) != len(all_rows):
        raise ValueError('GAMER_G2_0019_TRAIN_DEV_LEAKAGE')
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
                               parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_original={key:value.detach().cpu().clone() for key,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    model,tokenizer=backbone.model_components(manifest['model_id'],
                                               manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0019_BACKBONE_UNFROZEN')
    captures={}
    for label,rows in [('train',training),('calibration',calibration),
                       (ceiling17.SPLITS[0],independent),
                       (ceiling17.SPLITS[1],withheld)]:
        captures[label]=rel.capture(head,model,tokenizer,rows,device=device,
                                     max_length=max_length,group=label)
    width=captures['train'][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1]!=width for cases in captures.values() for case in cases):
        raise ValueError('GAMER_G2_0019_WIDTH_DRIFT')
    baseline=ceiling17.load_general_scorer(Path(general_run)/general16.WEIGHTS,
                    width,report16['new_weights_sha256']).to(device)
    baseline_state={key:value.detach().cpu().clone() for key,value in baseline.state_dict().items()}
    allowed=sel.compatibility_from_train(original)
    for row,case in zip(training,captures['train']):
        case.update({'rank':rel.full_logits(head,case['vectors'].to(device)).detach().cpu(),
                     'allowed':allowed,'structural_topology':row.get('structural_topology','native'),
                     'split':row['split']})
    trained,train_info=train(captures['train'],baseline,device=device,steps=steps)
    groups={}
    for label,rows in [('calibration',calibration),
                       (ceiling17.SPLITS[0],independent),
                       (ceiling17.SPLITS[1],withheld)]:
        saved=[]
        for index,(case,row,old) in enumerate(zip(captures[label],rows,records16[label]),1):
            saved.append(evaluate_case(case,row,old,head,baseline,trained,allowed,
                                       group=label,index=index,device=device))
            if index==1 or index%40==0 or index==len(rows):
                print(f'GAMER_G2_0019_EVALUATE {label} {index}/{len(rows)}',flush=True)
        if len(saved)!=len(rows):raise ValueError('GAMER_G2_0019_EVAL_CASE_COUNT')
        groups[label]=saved
    rel.validate_original_state(head,head_original)
    if any(not torch.equal(baseline.state_dict()[k].detach().cpu(),v)
           for k,v in baseline_state.items()):
        raise ValueError('GAMER_G2_0019_BASELINE_MUTATED')
    result={
        'schema':SCHEMA,'status':'G2_INFERENCE_REALISTIC_TRAINING_DEV_NOT_CERTIFIED',
        'parent_0016_selected':ceiling17.GENERAL,'fixed_threshold':THRESHOLD,
        'candidate_top_k':TOP_K,'seed':SEED,'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{label:density.sha_rows(rows) for label,rows in (
            ('calibration',calibration),(ceiling17.SPLITS[0],independent),
            (ceiling17.SPLITS[1],withheld))},
        'parent_hashes':{**hashes16,**hashes17,**hashes18},
        'train':train_info,
        'evaluation':{label:aggregate(rows) for label,rows in groups.items()},
        'by_family':{label:{family:aggregate([r for r in rows if r['family']==family])
                            for family in ('encounter','allocation')
                            if any(r['family']==family for r in rows)}
                     for label,rows in groups.items()},
        'by_density':{label:{bucket:aggregate([r for r in rows if r['density_bucket']==bucket])
                             for bucket in sorted({r['density_bucket'] for r in rows})}
                      for label,rows in groups.items()},
        'by_topology':{top:aggregate([r for r in groups[ceiling17.SPLITS[1]]
                                     if r['structural_topology']==top])
                       for top in ('2x4','4x2','4x4')},
        'diagnostic_gates':predeclared_gates(groups),
        'train_arms':list(ARMS[1:]),'baseline_0016_frozen':True,
        'g1_frozen':True,'backbone_frozen':True,'rank_head_frozen':True,
        'original_training_data_only':True,'train_inventory_gold_injection_disabled':True,
        'candidate_and_group_selection_label_blind':True,
        'selection_on_exposed_dev':False,'checkpoint_selected':False,
        'no_density_specific_inference_router':True,
        'previously_exposed_development_only':True,
        'withheld_topologies_dev_only':True,'cross_domain_generality_not_claimed':True,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'certification_authorized':False,'memory_writes':False,
    }
    output.mkdir(parents=True,exist_ok=False)
    save_file({f'{arm}.{key}':tensor.detach().cpu().contiguous()
               for arm,net in trained.items() for key,tensor in net.state_dict().items()},
              str(output/WEIGHTS))
    result['new_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl([row for group in SPLITS for row in groups[group]]))
    return result
