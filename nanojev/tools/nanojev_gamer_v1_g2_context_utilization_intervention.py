"""G2-0025: frozen-base branch-only context-utilization intervention.

Two residual branches share the same G2-0023 context checkpoint, reset readout,
TRAIN batches, loss and optimizer. Only access to other candidates differs.
There is no model selection, checkpoint promotion or challenge/test access.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import gzip
import json
import random

import torch
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
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as ceiling21
from tools import nanojev_gamer_v1_g2_competition_aware_learning as parent22
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24

SCHEMA='nanojev.gamer.graph.g2.context-utilization-intervention.v1'
REPORT='g2_context_utilization_intervention_report.json'
CASES='g2_context_utilization_intervention_per_case.jsonl.gz'
WEIGHTS='g2_context_utilization_intervention_development.safetensors'
SEED=460030
EVAL_SEED=460030
BATCH_SEED=parent22.SEED
STEPS=480
LR=3.e-4
TOP_K=parent23.TOP_K
THRESHOLD=parent23.THRESHOLD
GROUPS_PER_CASE=parent23.GROUPS_PER_CASE
REPLAY=parent23.NEW
NEW=('new_independent_dev_0025','new_topology_dev_0025')
SPLITS=(*REPLAY,*NEW)
ARMS=('frozen_context_0023','self_only_0025','peer_context_0025')
TRAINED=ARMS[1:]
ABLATIONS=('peer_suppressed','uniform_attention','misaligned_peer_values',
           'residual_disabled')


def verify_parent24(directory, rows, expected_hashes):
    """Revalidate immutable audit inputs/aggregates, not just its stated result."""
    directory=Path(directory)
    rp,cp=directory/audit24.REPORT,directory/audit24.CASES
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=audit24.SCHEMA:
        raise ValueError('GAMER_G2_0025_PARENT_0024_CONTRACT:schema')
    expected={
        'schema':audit24.SCHEMA,
        'status':'G2_CONTEXT_UTILIZATION_DIAGNOSTIC_NOT_CERTIFIED',
        'parent_hashes':expected_hashes,
        'datasets_sha256':{k:density.sha_rows(rows[k]) for k in REPLAY},
        'predeclared':{'cohorts':list(REPLAY),'fixed_threshold':THRESHOLD,
            'top_k':TOP_K,'variants':list(audit24.VARIANTS),
            'gradient_indices':list(audit24.GRADIENT_INDICES),
            'parent_model':'G2-0023 relational_context_0023',
            'counterfactual_only_no_inference_changes':True},
        'frozen_checkpoint_sha256':expected_hashes['g2_0023_weights_sha256'],
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,**audit24._no_promote(),
    }
    for field,value in expected.items():
        if type(report.get(field)) is not type(value) or report[field]!=value:
            raise ValueError('GAMER_G2_0025_PARENT_0024_CONTRACT:'+field)
    if report.get('cases_sha256')!=backbone.sha256(cp):
        raise ValueError('GAMER_G2_0025_PARENT_0024_HASH_DRIFT')
    if (set(report.get('parameters',{}))!=set(parent23.TRAINED) or
            set(report.get('gradient_probes',{}))!=set(REPLAY)):
        raise ValueError('GAMER_G2_0025_PARENT_0024_AUDIT_FIELDS')
    grouped={k:[] for k in REPLAY}
    with gzip.open(cp,'rt',encoding='utf-8') as f:
        for line in f:
            r=json.loads(line)
            split=r.get('group')
            if split not in grouped:raise ValueError('GAMER_G2_0025_PARENT_0024_EXTRA_SPLIT')
            i=len(grouped[split]);original=rows[split]
            if i>=len(original):raise ValueError('GAMER_G2_0025_PARENT_0024_COUNT')
            row=original[i]
            if (r.get('index')!=i+1 or r.get('prompt_sha256')!=base._sha(row['prompt'])
                or r.get('family')!=row['family']
                or r.get('structural_topology')!=row.get('structural_topology','native')
                or r.get('gold_total')!=len(base.gold_binding_edges(row))
                or r.get('density_bucket') not in ('small_1_24','medium_25_48','crowded_49_128')
                or set(r.get('metrics',{}))!=set(audit24.VARIANTS)
                or set(r.get('competition',{}))!=set(audit24.VARIANTS)
                or set(r.get('shifts',{}))!=set(audit24.VARIANTS[1:])
                or type(r.get('candidates')) is not int or r['candidates']<0):
                raise ValueError('GAMER_G2_0025_PARENT_0024_LEDGER_DRIFT')
            grouped[split].append(r)
    if any(len(grouped[k])!=len(rows[k]) for k in REPLAY):
        raise ValueError('GAMER_G2_0025_PARENT_0024_COUNT')
    combined=[r for k in REPLAY for r in grouped[k]]
    checks={
        'evaluation':audit24.aggregate(combined),
        'by_split':{k:audit24.aggregate(grouped[k]) for k in REPLAY},
        'by_family':{f:audit24.aggregate([r for r in combined if r['family']==f])
                    for f in sorted({r['family'] for r in combined})},
        'by_density':{d:audit24.aggregate([r for r in combined if r['density_bucket']==d])
                     for d in sorted({r['density_bucket'] for r in combined})},
        'by_topology':{t:audit24.aggregate([r for r in combined if r['structural_topology']==t])
                      for t in sorted({r['structural_topology'] for r in combined})},
    }
    for field,value in checks.items():
        if report.get(field)!=value:
            raise ValueError('GAMER_G2_0025_PARENT_0024_AGGREGATE_DRIFT:'+field)
    return report,grouped,{'g2_0024_report_sha256':backbone.sha256(rp),
                           'g2_0024_cases_sha256':backbone.sha256(cp)}


def fresh_cohorts(prior,training):
    """Use new seeds; exclude every prior TRAIN/DEV prompt through G2-0023."""
    independent=general16.independent_rows((prior,),limit=320,seed=EVAL_SEED)
    topology=general16.heldout_topology_rows((prior,independent),limit=128,
                                              seed=EVAL_SEED+1)
    combined=[*prior,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128 or
        len({base._sha(r['prompt']) for r in combined})!=len(combined) or
        {r.get('structural_topology') for r in topology}!={'2x4','4x2','4x4'} or
        any(r.get('split')!='dev' for r in (*independent,*topology)) or
        any(r.get('split')!='train' for r in training)):
        raise ValueError('GAMER_G2_0025_DEV_DISJOINTNESS')
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
        raise ValueError('GAMER_G2_0025_INITIALIZATION_MISMATCH')
    return arms


def _batch_objective(net,batch,*,contextual):
    losses=[]
    for x,b,g,y,null,far in batch:
        logits=net(x,b,g,with_competition=True,contextual=contextual)
        losses.append(train19.training_objective(logits,y,null,far,g,arm='precision_guard'))
    return torch.stack(losses).mean()


def _train(cases,parent,*,device='cpu',steps=STEPS,lr=LR,seed=SEED,
           inventory_fn=source13.make_inventory):
    if (steps!=STEPS or lr!=LR or seed!=SEED or len(cases)!=640 or
        {r['family'] for r in cases}!={'encounter','allocation'} or
        any(r.get('split')!='train' or r.get('structural_topology') in ('2x4','4x2','4x4')
            for r in cases)):
        raise ValueError('GAMER_G2_0025_FIXED_TRAIN_CONTRACT')
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
            raise ValueError('GAMER_G2_0025_DUPLICATE_TRAIN_CANDIDATES')
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
        if not batch:raise ValueError('GAMER_G2_0025_EMPTY_BATCH')
        for arm,net in arms.items():
            loss=_batch_objective(net,batch,contextual=arm==TRAINED[1])
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0025_NONFINITE_LOSS')
            optim[arm].zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in net.parameters() if p.requires_grad],1.)
            optim[arm].step();last[arm]=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0025_TRAIN {step+1}/{steps} '
                  f'self={last[TRAINED[0]]:.6f} context={last[TRAINED[1]]:.6f}',flush=True)
    for arm,net in arms.items():
        net.eval().requires_grad_(False)
        if any(not torch.equal(net.state_dict()[k].detach().cpu(),v)
               for k,v in frozen[arm].items()):
            raise ValueError('GAMER_G2_0025_FROZEN_BASE_MUTATED')
        if not torch.isfinite(torch.stack([p.detach().abs().max() for p in net.parameters()])).all():
            raise ValueError('GAMER_G2_0025_NONFINITE_WEIGHTS')
    if any(not torch.equal(parent.state_dict()[k].detach().cpu(),v)
           for k,v in parent_before.items()):
        raise ValueError('GAMER_G2_0025_PARENT_MUTATED')
    return arms,{'steps':steps,'lr':lr,'seed':seed,'batch_seed':BATCH_SEED,
        'train_cases':len(cases),'counts':dict(counts),'last_train_loss':last,
        'same_initialized_weights':True,'zero_reset_readouts':True,
        'frozen_trained_g2_0023_base':True,'residual_parameters_only':True,
        'identical_train_batches':True,'identical_loss':True,
        'candidate_inventory_label_blind':True,'multi_positive_labels':True}


def _evaluate_case(case,row,head,nets,allowed,*,split,index,device,parent=None):
    if (case['prompt_sha256']!=base._sha(row['prompt']) or case['family']!=row['family']):
        raise ValueError('GAMER_G2_0025_CASE_IDENTITY')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        if len(inv['edges'])!=len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0025_DUPLICATE_EVAL_CANDIDATES')
        x=inv['features'].to(device);b=inv['base'].to(device);g=inv['groups']
        scores={arm:net(x,b,g,with_competition=True,contextual=arm!=TRAINED[0]).detach().cpu()
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
        if parent is not None and (parent.get('prompt_sha256')!=case['prompt_sha256'] or
            parent.get('candidate_count')!=len(inv['edges']) or
            parent.get('reachable_gold')!=reachable or
            parent['configurations']['relational_context_0023']!=metrics[ARMS[0]] or
            parent['competition']['relational_context_0023']!=competition[ARMS[0]]):
            raise ValueError('GAMER_G2_0025_PARENT_0023_LIVE_REPLAY_DRIFT')
    return {'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
        'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
        'structural_topology':row.get('structural_topology','native'),
        'gold_total':len(case['gold']),'reachable_gold':reachable,
        'candidate_count':len(inv['edges']),'configurations':metrics,
        'competition':competition,'context_ablations':ablations}


def aggregate(records):
    if not records:raise ValueError('GAMER_G2_0025_EMPTY_AGGREGATE')
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
    m=data['metrics'];comp=data['competition'];ab=data['ablations']
    return {'context_f1_above_self_only':m[TRAINED[1]]['f1']>m[TRAINED[0]]['f1'],
        'context_f1_above_frozen_0023':m[TRAINED[1]]['f1']>m[ARMS[0]]['f1'],
        'context_precision_above_self_only':m[TRAINED[1]]['precision']>m[TRAINED[0]]['precision'],
        'context_recall_not_below_self_by_002':m[TRAINED[1]]['recall']>=m[TRAINED[0]]['recall']-.02,
        'context_rank_failures_below_self_only':
            comp[TRAINED[1]]['gold_not_ahead_of_hardest_negative']<
            comp[TRAINED[0]]['gold_not_ahead_of_hardest_negative'],
        'context_actual_peer_utilization':
            ab['peer_suppressed']['decision_flips']>0 and
            ab['misaligned_peer_values']['decision_flips']>0,
        'context_sensitive_attention_weights':ab['uniform_attention']['decision_flips']>0,
        'context_useful_over_peer_suppression':m[TRAINED[1]]['f1']>
            ab['peer_suppressed']['graph_metrics']['f1']}


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
        context23_run,audit24_run,output,device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0025_IMMUTABLE_OUTPUT')
    if steps!=STEPS or max_length!=2048:
        raise ValueError('GAMER_G2_0025_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0025_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0025_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0025_G1_LINEAGE')
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
        raise ValueError('GAMER_G2_0025_PARENT_0016_LINEAGE')
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
    if len(training)!=640:raise ValueError('GAMER_G2_0025_TRAIN_COUNT')
    all_previous=[training,calibration,independent,withheld,*fresh20.values(),
                  *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for batch in all_previous for r in batch})!=sum(map(len,all_previous)):
        raise ValueError('GAMER_G2_0025_PARENT_DEV_OVERLAP')
    parent23_report,old23,h23=audit24.verify_parent23(context23_run,previous,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22},density.sha_rows(training))
    parent24_report,old24,h24=verify_parent24(audit24_run,fresh23,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23})
    history25=[r for batch in all_previous for r in batch]
    fresh25=fresh_cohorts(history25,training)
    all_new=[*history25,*fresh25[NEW[0]],*fresh25[NEW[1]]]
    if len({base._sha(r['prompt']) for r in all_new})!=len(all_new):
        raise ValueError('GAMER_G2_0025_NEW_DEV_OVERLAP')
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
        parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0025_BACKBONE_UNFROZEN')
    allowed=sel.compatibility_from_train(original)
    to_evaluate={**fresh23,**fresh25}
    captured={}
    for split,rows in [('train',training),*to_evaluate.items()]:
        captured[split]=rel.capture(head,backbone_model,tokenizer,rows,
                                    device=device,max_length=max_length,group=split)
        if len(captured[split])!=len(rows):
            raise ValueError('GAMER_G2_0025_CAPTURE_COUNT')
    width=captured['train'][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1]!=width for group in captured.values() for case in group):
        raise ValueError('GAMER_G2_0025_WIDTH_DRIFT')
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
    trained,train_info=_train(captured['train'],parent,device=device,steps=steps)
    nets={ARMS[0]:parent,**trained}
    records={}
    for split,rows in to_evaluate.items():
        entries=[]
        for i,(case,row) in enumerate(zip(captured[split],rows),1):
            prior_record=old23[split][i-1] if split in REPLAY else None
            record=_evaluate_case(case,row,head,nets,allowed,split=split,index=i,
                                  device=device,parent=prior_record)
            if split in REPLAY:
                audit_record=old24[split][i-1]
                if (record['density_bucket']!=audit_record['density_bucket'] or
                    record['configurations'][ARMS[0]]!=audit_record['metrics']['full_context']
                    or record['competition'][ARMS[0]]!=audit_record['competition']['full_context']):
                    raise ValueError('GAMER_G2_0025_PARENT_0024_LIVE_REPLAY_DRIFT')
            entries.append(record)
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_0025_EVALUATE {split} {i}/{len(rows)}',flush=True)
        records[split]=entries
    rel.validate_original_state(head,head_before)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),v)
           for k,v in guard_before.items()):
        raise ValueError('GAMER_G2_0025_GUARD_MUTATED')
    if any(not torch.equal(parent.state_dict()[k].detach().cpu(),v)
           for k,v in parent_before.items()):
        raise ValueError('GAMER_G2_0025_PARENT_MUTATED')
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0025_BACKBONE_TRAINED')
    cohorts={'replayed_0023_dev':[r for key in REPLAY for r in records[key]],
             'new_0025_dev':[r for key in NEW for r in records[key]]}
    results={name:aggregate(rows) for name,rows in cohorts.items()}
    result={'schema':SCHEMA,'status':'G2_CONTEXT_BRANCH_TRAIN_ONLY_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(rows) for k,rows in to_evaluate.items()},
        'new_prompt_sha256':{k:[base._sha(row['prompt']) for row in fresh25[k]] for k in NEW},
        'predeclared':{'train_seed':SEED,'new_dev_seeds':[EVAL_SEED,EVAL_SEED+1],
            'steps':STEPS,'lr':LR,'batch_seed':BATCH_SEED,
            'top_k':TOP_K,'groups_per_case':GROUPS_PER_CASE,
            'fixed_threshold':THRESHOLD,'arms':list(ARMS),
            'ablations':list(ABLATIONS),
            'base_checkpoint':'G2-0023 relational_context_0023',
            'initial_residual_readout':'zero_both_arms',
            'loss':'G2-0019 precision_guard unchanged',
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
