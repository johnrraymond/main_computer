"""G2-0026: frozen-base competitor-identity ranking intervention.

An identity-sensitive, within-source multi-positive hard-negative objective trains
the same frozen-base residual branch with and without peer features. Previous
G2-0025 arms are replayed only as immutable development controls. Never promote.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import gzip
import json
import random

import torch
from safetensors.torch import load_file, save_file
from torch.nn import functional as F

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_competitor_context_intervention as context18
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as ceiling21
from tools import nanojev_gamer_v1_g2_competition_aware_learning as parent22
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24
from tools import nanojev_gamer_v1_g2_context_utilization_intervention as parent25

SCHEMA='nanojev.gamer.graph.g2.competitor-identity-discrimination.v1'
REPORT='g2_competitor_identity_discrimination_report.json'
CASES='g2_competitor_identity_discrimination_per_case.jsonl.gz'
WEIGHTS='g2_competitor_identity_discrimination_development.safetensors'
SEED=460032
EVAL_SEED=460032
BATCH_SEED=parent25.BATCH_SEED
STEPS=480
LR=parent25.LR
TOP_K=parent25.TOP_K
THRESHOLD=parent25.THRESHOLD
GROUPS_PER_CASE=parent25.GROUPS_PER_CASE
PAIR_WEIGHT=0.75
PAIR_MARGIN=1.0
HARD_NEGATIVES=3
REPLAY=parent25.NEW
NEW=('new_independent_dev_0026','new_topology_dev_0026')
SPLITS=(*REPLAY,*NEW)
ARMS=(*parent25.ARMS,'identity_self_0026','identity_peer_0026')
TRAINED=ARMS[-2:]
ABLATIONS=parent25.ABLATIONS


def verify_parent24(directory, rows, expected_hashes):
    """Use G2-0025's audited verifier: audit cohorts are G2-0023 DEV, not 0025 DEV.

    G2-0026's REPLAY constant intentionally names G2-0025 cohorts. Never
    substitute it into the immutable G2-0024 verification contract.
    """
    return parent25.verify_parent24(directory, rows, expected_hashes)


def verify_parent25(directory, rows, expected_hashes, expected_train_hash):
    """Verify G2-0025 report, loss/control contract, all rows and saved states.

    Reconstruct aggregates from the gzip ledger instead of trusting the report.
    The live replay of all three G2-0025 arms follows in _evaluate_case.
    """
    directory=Path(directory)
    rp,cp,wp=directory/parent25.REPORT,directory/parent25.CASES,directory/parent25.WEIGHTS
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=parent25.SCHEMA:
        raise ValueError('GAMER_G2_0026_PARENT_0025_CONTRACT:schema')
    expected={
        'schema':parent25.SCHEMA,
        'status':'G2_CONTEXT_BRANCH_TRAIN_ONLY_NOT_CERTIFIED',
        'parent_hashes':expected_hashes,
        'train_dataset_sha256':expected_train_hash,
        'datasets_sha256':{k:density.sha_rows(rows[k]) for k in parent25.SPLITS},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in rows[k]] for k in parent25.NEW},
        'predeclared':{'train_seed':parent25.SEED,
            'new_dev_seeds':[parent25.EVAL_SEED,parent25.EVAL_SEED+1],
            'steps':parent25.STEPS,'lr':parent25.LR,'batch_seed':parent25.BATCH_SEED,
            'top_k':TOP_K,'groups_per_case':GROUPS_PER_CASE,
            'fixed_threshold':THRESHOLD,'arms':list(parent25.ARMS),
            'ablations':list(parent25.ABLATIONS),
            'base_checkpoint':'G2-0023 relational_context_0023',
            'initial_residual_readout':'zero_both_arms',
            'loss':'G2-0019 precision_guard unchanged',
            'training':'frozen_parent_scorer_residual_only',
            'self_only_control':'same_trainable_parameters_and_batches'},
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'train_labels_used_only_for_loss':True,'multi_positive_supervision':True,
        'same_train_batches':True,'same_initial_checkpoint':True,'same_loss':True,
        'same_trainable_parameter_count':True,'frozen_existing_scorer':True,
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'frozen_0024_audit':True,
        
        'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'memory_writes':False}
    for field,value in expected.items():
        if type(report.get(field)) is not type(value) or report[field]!=value:
            raise ValueError('GAMER_G2_0026_PARENT_0025_CONTRACT:'+field)
    if report.get('cases_sha256')!=backbone.sha256(cp) or report.get('new_weights_sha256')!=backbone.sha256(wp):
        raise ValueError('GAMER_G2_0026_PARENT_0025_HASH_DRIFT')
    train=report.get('train',{})
    expected_training={'steps':parent25.STEPS,'lr':parent25.LR,
        'seed':parent25.SEED,'batch_seed':parent25.BATCH_SEED,'train_cases':640,
        'same_initialized_weights':True,'zero_reset_readouts':True,
        'frozen_trained_g2_0023_base':True,'residual_parameters_only':True,
        'identical_train_batches':True,'identical_loss':True,
        'candidate_inventory_label_blind':True,'multi_positive_labels':True}
    for field,value in expected_training.items():
        if type(train.get(field)) is not type(value) or train[field]!=value:
            raise ValueError('GAMER_G2_0026_PARENT_0025_TRAIN_DRIFT:'+field)
    if set(train.get('last_train_loss',{}))!=set(parent25.TRAINED):
        raise ValueError('GAMER_G2_0026_PARENT_0025_TRAIN_LOSS_DRIFT')
    grouped={k:[] for k in parent25.SPLITS}
    with gzip.open(cp,'rt',encoding='utf-8') as f:
        for line in f:
            r=json.loads(line)
            split=r.get('group')
            if split not in grouped:raise ValueError('GAMER_G2_0026_PARENT_0025_EXTRA_SPLIT')
            i=len(grouped[split]);original=rows[split]
            if i>=len(original):raise ValueError('GAMER_G2_0026_PARENT_0025_CASE_COUNT')
            row=original[i]
            if (r.get('index')!=i+1 or r.get('prompt_sha256')!=base._sha(row['prompt'])
                or r.get('family')!=row['family']
                or r.get('structural_topology')!=row.get('structural_topology','native')
                or r.get('gold_total')!=len(base.gold_binding_edges(row))
                or r.get('density_bucket') not in ('small_1_24','medium_25_48','crowded_49_128')
                or set(r.get('configurations',{}))!=set(parent25.ARMS)
                or set(r.get('competition',{}))!=set(parent25.ARMS)
                or set(r.get('context_ablations',{}))!=set(parent25.ABLATIONS)
                or type(r.get('candidate_count')) is not int or r['candidate_count']<0):
                raise ValueError('GAMER_G2_0026_PARENT_0025_LEDGER_DRIFT')
            grouped[split].append(r)
    if any(len(grouped[k])!=len(rows[k]) for k in parent25.SPLITS):
        raise ValueError('GAMER_G2_0026_PARENT_0025_CASE_COUNT')
    cohorts={'replayed_0023_dev':[r for s in parent25.REPLAY for r in grouped[s]],
             'new_0025_dev':[r for s in parent25.NEW for r in grouped[s]]}
    checks={'by_split':{k:parent25.aggregate(v) for k,v in grouped.items()},
        'evaluation':{k:parent25.aggregate(v) for k,v in cohorts.items()},
        'by_family':{k:{f:parent25.aggregate([r for r in v if r['family']==f])
                        for f in sorted({r['family'] for r in v})} for k,v in cohorts.items()},
        'by_density':{k:{d:parent25.aggregate([r for r in v if r['density_bucket']==d])
                        for d in sorted({r['density_bucket'] for r in v})} for k,v in cohorts.items()},
        'by_topology':{k:{t:parent25.aggregate([r for r in v if r['structural_topology']==t])
                        for t in sorted({r['structural_topology'] for r in v})} for k,v in cohorts.items()},
        'diagnostic_gates':{k:parent25.gates(parent25.aggregate(v)) for k,v in cohorts.items()}}
    for field,value in checks.items():
        if report.get(field)!=value:
            raise ValueError('GAMER_G2_0026_PARENT_0025_AGGREGATE_DRIFT:'+field)
    # Validate the saved tensor inventory before training or evaluating anything.
    keys=load_file(str(wp),device='cpu')
    names={key[len(parent25.TRAINED[0])+1:] for key in keys
           if key.startswith(parent25.TRAINED[0]+'.')}
    if not names or set(keys)!={a+'.'+k for a in parent25.TRAINED for k in names}:
        raise ValueError('GAMER_G2_0026_PARENT_0025_WEIGHTS_KEYS')
    if any(not torch.isfinite(t).all() for t in keys.values()):
        raise ValueError('GAMER_G2_0026_PARENT_0025_NONFINITE')
    return report,grouped,{'g2_0025_report_sha256':backbone.sha256(rp),
                           'g2_0025_cases_sha256':backbone.sha256(cp),
                           'g2_0025_weights_sha256':backbone.sha256(wp)}


def load_parent25_models(directory,template,expected_hash):
    wp=Path(directory)/parent25.WEIGHTS
    if backbone.sha256(wp)!=expected_hash:
        raise ValueError('GAMER_G2_0026_PARENT_0025_WEIGHT_DRIFT')
    state=load_file(str(wp),device='cpu')
    expected={a+'.'+k for a in parent25.TRAINED for k in template.state_dict()}
    if set(state)!=expected:raise ValueError('GAMER_G2_0026_PARENT_0025_WEIGHTS_KEYS')
    models={}
    for arm in parent25.TRAINED:
        model=deepcopy(template).eval().requires_grad_(False)
        model.load_state_dict({k:state[arm+'.'+k] for k in template.state_dict()},strict=True)
        models[arm]=model
    return models


def hard_identity_loss(logits, labels, groups, *, margin=PAIR_MARGIN,
                       hard_negatives=HARD_NEGATIVES):
    """TRAIN-only, within-source multi-positive contrast against actual hard peers.

    Each positive must exceed the highest-scoring negatives of its own source
    and relation group. No positive-positive penalty, no cross-group comparisons.
    Returns zero connected to logits when a batch lacks mixed groups.
    """
    if logits.ndim!=1 or labels.shape!=logits.shape or len(groups)!=len(logits):
        raise ValueError('GAMER_G2_0026_IDENTITY_SHAPES')
    if any(groups[i]>groups[i+1] for i in range(len(groups)-1)):
        raise ValueError('GAMER_G2_0026_IDENTITY_GROUP_ORDER')
    if not (margin>0 and hard_negatives>=1):
        raise ValueError('GAMER_G2_0026_IDENTITY_PROTOCOL')
    loss=[];start=0
    while start<len(groups):
        end=start+1
        while end<len(groups) and groups[end]==groups[start]:end+=1
        pos=logits[start:end][labels[start:end]>.5]
        neg=logits[start:end][labels[start:end]<=.5]
        if pos.numel() and neg.numel():
            hard=neg.topk(min(hard_negatives,neg.numel())).values
            loss.append(F.softplus(margin+hard[None,:]-pos[:,None]).mean())
        start=end
    return torch.stack(loss).mean() if loss else logits.sum()*0.


def fresh_cohorts(prior,training):
    """Use new seeds; exclude every prior TRAIN/DEV prompt through G2-0025."""
    independent=general16.independent_rows((prior,),limit=320,seed=EVAL_SEED)
    topology=general16.heldout_topology_rows((prior,independent),limit=128,
                                              seed=EVAL_SEED+1)
    combined=[*prior,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128 or
        len({base._sha(r['prompt']) for r in combined})!=len(combined) or
        {r.get('structural_topology') for r in topology}!={'2x4','4x2','4x4'} or
        any(r.get('split')!='dev' for r in (*independent,*topology)) or
        any(r.get('split')!='train' for r in training)):
        raise ValueError('GAMER_G2_0026_DEV_DISJOINTNESS')
    return dict(zip(NEW,(independent,topology)))


def _base_snapshot(net):
    return {k:v.detach().cpu().clone() for k,v in net.state_dict().items()
            if not k.startswith('rel_')}


def initialize_arms(parent):
    """Same trained G2-0023 base and residual initialization in both arms.

    Reset only the residual readout so the initial predictions are identical
    to the trained, frozen G2-0023 base. The previous residual's learned
    ~constant offset is intentionally not inherited as a starting advantage.
    """
    template=deepcopy(parent).cpu()
    with torch.no_grad():
        template.rel_readout.weight.zero_();template.rel_readout.bias.zero_()
    arms={arm:deepcopy(template) for arm in TRAINED}
    for net in arms.values():
        for name,p in net.named_parameters():p.requires_grad_(name.startswith('rel_'))
    if any(not torch.equal(a,b) for a,b in zip(arms[TRAINED[0]].state_dict().values(),
                                               arms[TRAINED[1]].state_dict().values())):
        raise ValueError('GAMER_G2_0026_INITIALIZATION_MISMATCH')
    return arms


def _batch_objective(net,batch,*,contextual):
    losses=[]
    for x,b,g,y,null,far in batch:
        logits=net(x,b,g,with_competition=True,contextual=contextual)
        base_loss=train19.training_objective(logits,y,null,far,g,arm='precision_guard')
        losses.append(base_loss+PAIR_WEIGHT*hard_identity_loss(logits,y,g))
    return torch.stack(losses).mean()


def _train(cases,parent,*,device='cpu',steps=STEPS,lr=LR,seed=SEED,
           inventory_fn=source13.make_inventory):
    if (steps!=STEPS or lr!=LR or seed!=SEED or len(cases)!=640 or
        {r['family'] for r in cases}!={'encounter','allocation'} or
        any(r.get('split')!='train' or r.get('structural_topology') in ('2x4','4x2','4x4')
            for r in cases)):
        raise ValueError('GAMER_G2_0026_FIXED_TRAIN_CONTRACT')
    parent_before={k:v.detach().cpu().clone() for k,v in parent.state_dict().items()}
    parent.eval().requires_grad_(False)
    arms={arm:net.to(device).train() for arm,net in initialize_arms(parent).items()}
    frozen={arm:_base_snapshot(net) for arm,net in arms.items()}
    optim={arm:torch.optim.AdamW([p for p in net.parameters() if p.requires_grad],lr=lr)
           for arm,net in arms.items()}
    families={f:[i for i,r in enumerate(cases) if r['family']==f]
              for f in ('encounter','allocation')}
    rng=random.Random(BATCH_SEED)
    @lru_cache(maxsize=8)
    def inventory(i):
        r=cases[i];inv=inventory_fn(r,r['rank'],r['allowed'],top_k=TOP_K,train_gold=False)
        if len(inv['edges'])!=len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0026_DUPLICATE_TRAIN_CANDIDATES')
        return inv
    counts=Counter();last={}
    for step in range(steps):
        batch=[]
        for j in range(3):
            i=rng.choice(families[('encounter','allocation')[(step+j)%2]])
            inv=inventory(i)
            if not inv['edges']:continue
            ids=train19.label_blind_groups(inv,seed=BATCH_SEED+step*101+i*7+j,
                                           max_groups=GROUPS_PER_CASE)
            group=[inv['groups'][k] for k in ids]
            row=cases[i]
            labels=torch.tensor([float(inv['edges'][k] in row['gold']) for k in ids],device=device)
            null=torch.tensor([float(inv['null_argmax'][k]) for k in ids],device=device)
            far=torch.tensor([float(abs(inv['edges'][k][0][0]-inv['edges'][k][1][0])>128)
                              for k in ids],device=device)
            # Captures are inference tensors: a normal clone is safe as input
            # to autograd operations on learned branch parameters.
            x=inv['features'][ids].clone().detach().to(device)
            b=inv['base'][ids].clone().detach().to(device)
            batch.append((x,b,group,labels,null,far))
            counts['candidates']+=len(ids);counts['gold']+=int(labels.sum())
            counts['groups']+=len(set(group))
            per_group=defaultdict(lambda:[0,0])
            for g,value in zip(group,labels.detach().cpu().tolist()):
                per_group[g][int(value>.5)]+=1
            for negatives,positives in per_group.values():
                if negatives and positives:
                    counts['mixed_identity_groups']+=1
                    counts['hard_identity_pairs']+=positives*min(negatives,HARD_NEGATIVES)
        if not batch:raise ValueError('GAMER_G2_0026_EMPTY_BATCH')
        for arm,net in arms.items():
            loss=_batch_objective(net,batch,contextual=arm==TRAINED[1])
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0026_NONFINITE_LOSS')
            optim[arm].zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in net.parameters() if p.requires_grad],1.)
            optim[arm].step();last[arm]=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0026_TRAIN {step+1}/{steps} '
                  f'self={last[TRAINED[0]]:.6f} context={last[TRAINED[1]]:.6f}',flush=True)
    if counts['mixed_identity_groups']<=0 or counts['hard_identity_pairs']<=0:
        raise ValueError('GAMER_G2_0026_NO_HARD_IDENTITY_TRAINING_SIGNAL')
    for arm,net in arms.items():
        net.eval().requires_grad_(False)
        if any(not torch.equal(net.state_dict()[k].detach().cpu(),v)
               for k,v in frozen[arm].items()):
            raise ValueError('GAMER_G2_0026_FROZEN_BASE_MUTATED')
        if not torch.isfinite(torch.stack([p.detach().abs().max() for p in net.parameters()])).all():
            raise ValueError('GAMER_G2_0026_NONFINITE_WEIGHTS')
    if any(not torch.equal(parent.state_dict()[k].detach().cpu(),v)
           for k,v in parent_before.items()):
        raise ValueError('GAMER_G2_0026_PARENT_MUTATED')
    return arms,{'steps':steps,'lr':lr,'seed':seed,'batch_seed':BATCH_SEED,
        'train_cases':len(cases),'counts':dict(counts),'last_train_loss':last,
        'same_initialized_weights':True,'zero_reset_readouts':True,
        'frozen_trained_g2_0023_base':True,'residual_parameters_only':True,
        'identical_train_batches':True,'identical_loss':True,'hard_identity_weight':PAIR_WEIGHT,
        'hard_identity_margin':PAIR_MARGIN,'hard_identity_negatives':HARD_NEGATIVES,
        'identity_pairs_within_source_only':True,
        'candidate_inventory_label_blind':True,'multi_positive_labels':True}


def _evaluate_case(case,row,head,nets,allowed,*,split,index,device,parent=None):
    if (case['prompt_sha256']!=base._sha(row['prompt']) or case['family']!=row['family']):
        raise ValueError('GAMER_G2_0026_CASE_IDENTITY')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        if len(inv['edges'])!=len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0026_DUPLICATE_EVAL_CANDIDATES')
        x=inv['features'].to(device);b=inv['base'].to(device);g=inv['groups']
        scores={arm:net(x,b,g,with_competition=True,contextual=arm not in ('self_only_0025',TRAINED[0])).detach().cpu()
                for arm,net in nets.items()}
        cf={a:audit24._audit_forward(nets[TRAINED[1]],x,b,g,a).detach().cpu()
            for a in ABLATIONS}
        metrics={arm:sel.score_set(source13.decode_scores(inv,scores[arm],THRESHOLD),case['gold'])
                 for arm in ARMS}
        competition={arm:parent22._competition_failures(inv['edges'],g,scores[arm],case['gold'])
                     for arm in ARMS}
        ablations={a:{'metrics':sel.score_set(source13.decode_scores(inv,cf[a],THRESHOLD),case['gold']),
                      'competition':parent22._competition_failures(inv['edges'],g,cf[a],case['gold']),
                      'mean_absolute_logit_shift':float((scores[TRAINED[1]]-cf[a]).abs().mean())
                        if scores[TRAINED[1]].numel() else 0.,
                      'max_absolute_logit_shift':float((scores[TRAINED[1]]-cf[a]).abs().max())
                        if scores[TRAINED[1]].numel() else 0.,
                      'decision_flips':len(source13.decode_scores(inv,scores[TRAINED[1]],THRESHOLD)
                                           ^source13.decode_scores(inv,cf[a],THRESHOLD))}
                    for a in ABLATIONS}
        reachable=len(set(inv['edges'])&case['gold'])
        if parent is not None:
            if (parent.get('prompt_sha256')!=case['prompt_sha256'] or
                parent.get('candidate_count')!=len(inv['edges']) or
                parent.get('reachable_gold')!=reachable or
                any(parent['configurations'][arm]!=metrics[arm] or
                    parent['competition'][arm]!=competition[arm] for arm in parent25.ARMS)):
                raise ValueError('GAMER_G2_0026_PARENT_0025_LIVE_REPLAY_DRIFT')

    return {'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
        'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
        'structural_topology':row.get('structural_topology','native'),
        'gold_total':len(case['gold']),'reachable_gold':reachable,
        'candidate_count':len(inv['edges']),'configurations':metrics,
        'competition':competition,'context_ablations':ablations}


def aggregate(records):
    if not records:raise ValueError('GAMER_G2_0026_EMPTY_AGGREGATE')
    totals={'cases':len(records),'gold_total':sum(r['gold_total'] for r in records),
            'reachable_gold':sum(r['reachable_gold'] for r in records),
            'candidates':sum(r['candidate_count'] for r in records)}
    totals['metrics']={a:base.metrics([{**r['configurations'][a],'family':r['family']}
                                     for r in records]) for a in ARMS}
    totals['competition']={a:{k:sum(r['competition'][a][k] for r in records)
                            for k in ('gold_with_competing_negatives',
                                      'gold_not_ahead_of_hardest_negative')}
                           for a in ARMS}
    totals['ablations']={a:{'graph_metrics':base.metrics([
        {**r['context_ablations'][a]['metrics'],'family':r['family']} for r in records]),
        'ranking_failures':sum(r['context_ablations'][a]['competition'][
            'gold_not_ahead_of_hardest_negative'] for r in records),
        'decision_flips':sum(r['context_ablations'][a]['decision_flips'] for r in records),
        'mean_absolute_logit_shift':sum(r['context_ablations'][a]['mean_absolute_logit_shift']*
                                         r['candidate_count'] for r in records)/max(totals['candidates'],1),
        'max_absolute_logit_shift':max(r['context_ablations'][a]['max_absolute_logit_shift']
                                       for r in records)} for a in ABLATIONS}
    return totals


def gates(data):
    m=data['metrics'];c=data['competition'];ab=data['ablations'];peer=TRAINED[1];self=TRAINED[0]
    failures=lambda a:c[a]['gold_not_ahead_of_hardest_negative']
    return {'identity_peer_f1_above_identity_self_by_005':m[peer]['f1']>=m[self]['f1']+.005,
        'identity_peer_f1_above_frozen_0023':m[peer]['f1']>m[ARMS[0]]['f1'],
        'identity_peer_f1_above_prior_peer_0025':m[peer]['f1']>m['peer_context_0025']['f1'],
        'identity_peer_ranking_beats_identity_self':failures(peer)<failures(self),
        'identity_peer_ranking_beats_prior_peer':failures(peer)<failures('peer_context_0025'),
        'competitor_identity_perturbation_changes_decisions':
            ab['misaligned_peer_values']['decision_flips']>0 and
            ab['uniform_attention']['decision_flips']>0,
        'competitor_identity_values_help_graph_f1':
            m[peer]['f1']>ab['misaligned_peer_values']['graph_metrics']['f1'],
        'competitor_identity_attention_helps_graph_f1':
            m[peer]['f1']>ab['uniform_attention']['graph_metrics']['f1'],
        'peer_features_help_vs_suppression':
            m[peer]['f1']>ab['peer_suppressed']['graph_metrics']['f1']}


def _save_cases(groups,path):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
            for split in SPLITS:
                for r in groups[split]:
                    z.write((json.dumps(r,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,competition22_run,
        context23_run,audit24_run,intervention25_run,output,device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0026_IMMUTABLE_OUTPUT')
    if steps!=STEPS or max_length!=2048:
        raise ValueError('GAMER_G2_0026_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0026_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0026_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0026_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,
        specialization_run=specialization_run)
    training=original+augmented
    report16,rec16,h16=ceiling17.verify_frozen_0016(general_run,source_run,
        routing_robustness_run,augmented,training,calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256']!=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256']!=parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0026_PARENT_0016_LINEAGE')
    exposed=dict(zip(ceiling20.EXPOSED,(independent,withheld)))
    rec17,h17=context18.verify_parent_0017(ceiling_run,exposed,h16)
    h18=train19.verify_parent_0018(context18_run,exposed,h16,h17,rec17)
    _,h19=ceiling20.verify_parent_0019(train19_run,{'calibration':calibration,**exposed},rec16,
        {**h16,**h17,**h18},expected_train_sha=density.sha_rows(training),
        expected_datasets={s:density.sha_rows(rows) for s,rows in
                           (('calibration',calibration),*exposed.items())})
    prior=[r for batch in (*history,augmented,calibration,independent,withheld) for r in batch]
    fresh20=ceiling20.fresh_cohorts(prior)
    _,h20=ceiling21.verify_parent_0020(ceiling20_run,{**exposed,**fresh20},
                                       {**h16,**h17,**h18,**h19})
    fresh21=ceiling21._cohort_rows(prior,fresh20)
    _,_,h21=parent22._read_parent21(ceiling21_run,{**fresh20,**fresh21},
                                   {**h16,**h17,**h18,**h19,**h20})
    fresh22=parent22.fresh_cohorts(prior,fresh20,fresh21)
    _,_,h22=parent23._verify_parent22(competition22_run,{**fresh21,**fresh22},
        {**h16,**h17,**h18,**h19,**h20,**h21},density.sha_rows(training))
    fresh23=parent23.fresh_cohorts(prior,fresh20,fresh21,fresh22)
    previous={**fresh22,**fresh23}
    if len(training)!=640:raise ValueError('GAMER_G2_0026_TRAIN_COUNT')
    all_previous=[training,calibration,independent,withheld,*fresh20.values(),
                  *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for batch in all_previous for r in batch})!=sum(map(len,all_previous)):
        raise ValueError('GAMER_G2_0026_PARENT_DEV_OVERLAP')
    parent23_report,old23,h23=audit24.verify_parent23(context23_run,previous,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22},density.sha_rows(training))
    parent24_report,old24,h24=verify_parent24(audit24_run,fresh23,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23})
    history25=[r for batch in all_previous for r in batch]
    fresh25=parent25.fresh_cohorts(history25,training)
    old25_parent_rows={**fresh23,**fresh25}
    parent25_report,old25,h25=verify_parent25(intervention25_run,old25_parent_rows,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24},
        density.sha_rows(training))
    history26=[*history25,*(r for batch in fresh25.values() for r in batch)]
    fresh26=fresh_cohorts(history26,training)
    all_new=[*history26,*fresh26[NEW[0]],*fresh26[NEW[1]]]
    if len({base._sha(r['prompt']) for r in all_new})!=len(all_new):
        raise ValueError('GAMER_G2_0026_NEW_DEV_OVERLAP')
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
        parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0026_BACKBONE_UNFROZEN')
    allowed=sel.compatibility_from_train(original)
    to_evaluate={**fresh25,**fresh26}
    captured={}
    for split,rows in [('train',training),*to_evaluate.items()]:
        captured[split]=rel.capture(head,backbone_model,tokenizer,rows,
                                    device=device,max_length=max_length,group=split)
        if len(captured[split])!=len(rows):
            raise ValueError('GAMER_G2_0026_CAPTURE_COUNT')
    width=captured['train'][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1]!=width for group in captured.values() for case in group):
        raise ValueError('GAMER_G2_0026_WIDTH_DRIFT')
    parent19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
    guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                parent19['new_weights_sha256'])
    guard_before={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
    controls=audit24.load_parent23_models(context23_run,guard,
                                         parent23_report['new_weights_sha256'])
    parent=controls['relational_context_0023'].to(device)
    parent_before={k:v.detach().cpu().clone() for k,v in parent.state_dict().items()}
    for row,case in zip(training,captured['train']):
        case.update({'rank':rel.full_logits(head,case['vectors'].to(device)).detach().cpu(),
            'allowed':allowed,'split':row['split'],
            'structural_topology':row.get('structural_topology','native')})
    previous_models=load_parent25_models(intervention25_run,parent,
                                          parent25_report['new_weights_sha256'])
    previous_before={arm:{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
                     for arm,net in previous_models.items()}
    trained,train_info=_train(captured['train'],parent,device=device,steps=steps)
    nets={ARMS[0]:parent,**previous_models,**trained}
    for net in previous_models.values():net.to(device).eval().requires_grad_(False)
    records={}
    for split,rows in to_evaluate.items():
        entries=[]
        for i,(case,row) in enumerate(zip(captured[split],rows),1):
            prior_record=old25[split][i-1] if split in REPLAY else None
            record=_evaluate_case(case,row,head,nets,allowed,split=split,index=i,
                                  device=device,parent=prior_record)
            entries.append(record)
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_0026_EVALUATE {split} {i}/{len(rows)}',flush=True)
        records[split]=entries
    rel.validate_original_state(head,head_before)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),v)
           for k,v in guard_before.items()):
        raise ValueError('GAMER_G2_0026_GUARD_MUTATED')
    if any(not torch.equal(parent.state_dict()[k].detach().cpu(),v)
           for k,v in parent_before.items()):
        raise ValueError('GAMER_G2_0026_PARENT_MUTATED')
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0026_BACKBONE_TRAINED')
    for arm,net in previous_models.items():
        if any(not torch.equal(net.state_dict()[k].detach().cpu(),v)
               for k,v in previous_before[arm].items()):
            raise ValueError('GAMER_G2_0026_PARENT_0025_MUTATED')
    cohorts={'replayed_0025_dev':[r for key in REPLAY for r in records[key]],
             'new_0026_dev':[r for key in NEW for r in records[key]]}
    results={name:aggregate(rows) for name,rows in cohorts.items()}
    result={'schema':SCHEMA,'status':'G2_COMPETITOR_IDENTITY_TRAIN_ONLY_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(rows) for k,rows in to_evaluate.items()},
        'new_prompt_sha256':{k:[base._sha(row['prompt']) for row in fresh26[k]] for k in NEW},
        'predeclared':{'train_seed':SEED,'new_dev_seeds':[EVAL_SEED,EVAL_SEED+1],
            'steps':STEPS,'lr':LR,'batch_seed':BATCH_SEED,
            'top_k':TOP_K,'groups_per_case':GROUPS_PER_CASE,
            'fixed_threshold':THRESHOLD,'arms':list(ARMS),
            'ablations':list(ABLATIONS),
            'base_checkpoint':'G2-0023 relational_context_0023',
            'initial_residual_readout':'zero_both_arms',
            'loss':'G2-0019 precision_guard + within-group identity contrast',
            'pair_weight':PAIR_WEIGHT,'pair_margin':PAIR_MARGIN,
            'hard_negatives_per_group':HARD_NEGATIVES,
            'training':'frozen_parent_scorer_residual_only',
            'self_only_control':'same_trainable_parameters_and_batches'},
        'train':train_info,
        'evaluation':results,
        'by_split':{k:aggregate(rows) for k,rows in records.items()},
        'by_family':{k:{f:aggregate([r for r in rows if r['family']==f])
                      for f in sorted({r['family'] for r in rows})}
                     for k,rows in cohorts.items()},
        'by_density':{k:{d:aggregate([r for r in rows if r['density_bucket']==d])
                       for d in sorted({r['density_bucket'] for r in rows})}
                      for k,rows in cohorts.items()},
        'by_topology':{k:{t:aggregate([r for r in rows if r['structural_topology']==t])
                        for t in sorted({r['structural_topology'] for r in rows})}
                       for k,rows in cohorts.items()},
        'diagnostic_gates':{k:gates(value) for k,value in results.items()},
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'train_labels_used_only_for_loss':True,'multi_positive_supervision':True,
        'same_train_batches':True,'same_initial_checkpoint':True,'same_loss':True,
        'same_trainable_parameter_count':True,'frozen_existing_scorer':True,
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'frozen_0024_audit':True,
        'frozen_0025_checkpoints':True,
        'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    save_file({arm+'.'+k:v.detach().cpu().contiguous() for arm,net in trained.items()
               for k,v in net.state_dict().items()},str(output/WEIGHTS))
    result['new_weights_sha256']=backbone.sha256(output/WEIGHTS)
    _save_cases(records,output/CASES)
    result['cases_sha256']=backbone.sha256(output/CASES)
    (output/REPORT).write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n',
                               encoding='utf-8')
    return result
