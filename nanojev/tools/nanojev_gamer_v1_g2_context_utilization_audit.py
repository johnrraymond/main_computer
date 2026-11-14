"""G2-0024: immutable, frozen-checkpoint audit of G2-0023 context utilization.

No training, threshold tuning, architecture changes or checkpoint writes. The
contextual model's saved weights are evaluated under explicitly diagnostic
counterfactuals against its own unchanged scoring path.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import gzip
import json
import math

import torch
from safetensors.torch import load_file

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

SCHEMA='nanojev.gamer.graph.g2.context-utilization-audit.v1'
REPORT='g2_context_utilization_audit_report.json'
CASES='g2_context_utilization_audit_per_case.jsonl.gz'
VARIANTS=('full_context','residual_disabled','peer_suppressed',
          'uniform_attention','misaligned_peer_values','self_only_counterfactual')
SPLITS=parent23.NEW
THRESHOLD=parent23.THRESHOLD
TOP_K=parent23.TOP_K
# Gradient sampling is fixed before results are inspected; never select cases by label.
GRADIENT_INDICES=(1,40,80,120,160,200,240,280,320)


def _no_promote():
    return {'model_selection':False,'checkpoint_selection':False,
        'training_performed':False,'threshold_selection':False,
        'production_threshold_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'certification_authorized':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False}


def verify_parent23(directory,rows,expected_hashes,train_sha):
    """Recompute all G2-0023 ledger aggregates and verify checkpoint provenance.

    The parent ledger contains decisions, not candidate logits; those are
    independently replayed during this audit, never trusted as feature input.
    """
    directory=Path(directory)
    report_path=directory/parent23.REPORT
    case_path=directory/parent23.CASES
    weight_path=directory/parent23.WEIGHTS
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('schema')!=parent23.SCHEMA:
        raise ValueError('GAMER_G2_0024_PARENT_0023_CONTRACT:schema')
    expected={'schema':parent23.SCHEMA,
        'status':'G2_CONTEXTUAL_RELATIONSHIP_DEV_NOT_CERTIFIED',
        'parent_hashes':expected_hashes,
        'train_dataset_sha256':train_sha,
        'datasets_sha256':{k:density.sha_rows(rows[k]) for k in parent23.SPLITS},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in rows[k]] for k in parent23.NEW},
        'predeclared':{'train_seed':parent23.SEED,
            'prior_0022_training_batch_seed':parent22.SEED,
            'new_dev_seeds':[parent23.EVAL_SEED,parent23.EVAL_SEED+1],
            'steps':parent23.STEPS,'lr':parent23.LR,
            'top_k':TOP_K,'groups_per_case':parent23.GROUPS_PER_CASE,
            'fixed_threshold':THRESHOLD,'arms':list(parent23.ARMS),
            'architecture':'candidate_specific_cross_attention_residual',
            'capacity_control':'same_parameters_candidate_self_only',
            'loss':'g2_0019_precision_guard_unmodified',
            'single_valid_destination_assumption':False},
        'candidate_selection_uses_gold':False,'gold_used_for_train_loss_only':True,
        'multi_positive_supervision':True,'same_train_batches':True,
        'same_initial_checkpoint':True,'same_loss_for_trained_arms':True,
        'same_trainable_parameter_count':True,
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_parent_guard':True,'frozen_0022_controls':True,
        'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'memory_writes':False}
    for name,expected_value in expected.items():
        if type(report.get(name)) is not type(expected_value) or report[name]!=expected_value:
            raise ValueError('GAMER_G2_0024_PARENT_0023_CONTRACT:'+name)
    if (report.get('cases_sha256')!=backbone.sha256(case_path) or
        report.get('new_weights_sha256')!=backbone.sha256(weight_path)):
        raise ValueError('GAMER_G2_0024_PARENT_0023_HASH_DRIFT')
    train=report.get('train')
    if not isinstance(train,dict) or any(train.get(name)!=value for name,value in {
            'steps':parent23.STEPS,'lr':parent23.LR,'seed':parent23.SEED,
            'batch_seed':parent22.SEED,'train_cases':640,
            'same_initialization':True,'same_training_batches':True,
            'same_precision_guard_loss':True,'no_new_training_gold_injection':True,
            'capacity_matched_trainable_parameters':True}.items()):
        raise ValueError('GAMER_G2_0024_PARENT_0023_TRAIN_DRIFT')
    grouped={k:[] for k in parent23.SPLITS}
    with gzip.open(case_path,'rt',encoding='utf-8') as f:
        for line in f:
            record=json.loads(line)
            key=record.get('group')
            if key not in grouped:raise ValueError('GAMER_G2_0024_PARENT_EXTRA_SPLIT')
            i=len(grouped[key]);original=rows[key]
            if i>=len(original):raise ValueError('GAMER_G2_0024_PARENT_CASE_COUNT')
            row=original[i]
            if (record.get('index')!=i+1 or
                record.get('prompt_sha256')!=base._sha(row['prompt']) or
                record.get('family')!=row['family'] or
                record.get('structural_topology')!=row.get('structural_topology','native') or
                record.get('gold_total')!=len(base.gold_binding_edges(row)) or
                set(record.get('configurations',{}))!=set(parent23.ARMS) or
                set(record.get('competition',{}))!=set(parent23.ARMS) or
                not isinstance(record.get('candidate_count'),int) or
                record['candidate_count']<0 or
                not isinstance(record.get('reachable_gold'),int) or
                not 0<=record['reachable_gold']<=record['gold_total']):
                raise ValueError('GAMER_G2_0024_PARENT_0023_LEDGER_DRIFT')
            grouped[key].append(record)
    if any(len(grouped[k])!=len(rows[k]) for k in grouped):
        raise ValueError('GAMER_G2_0024_PARENT_CASE_COUNT')
    combined={'replayed_0022_dev':[r for k in parent23.REPLAY for r in grouped[k]],
              'new_0023_dev':[r for k in parent23.NEW for r in grouped[k]]}
    checks={
        'by_split':{k:parent23.aggregate(v) for k,v in grouped.items()},
        'evaluation':{k:parent23.aggregate(v) for k,v in combined.items()},
        'by_family':{k:{f:parent23.aggregate([r for r in data if r['family']==f])
            for f in ('encounter','allocation') if any(r['family']==f for r in data)}
            for k,data in combined.items()},
        'by_density':{k:{d:parent23.aggregate([r for r in data if r['density_bucket']==d])
            for d in sorted({r['density_bucket'] for r in data})}
            for k,data in combined.items()},
        'by_topology':{k:{t:parent23.aggregate([r for r in data if r['structural_topology']==t])
            for t in ('4x4','4x2','2x4') if any(r['structural_topology']==t for r in data)}
            for k,data in combined.items()},
        'diagnostic_gates':{k:parent23.gates(parent23.aggregate(data)) for k,data in combined.items()},
    }
    for name,value in checks.items():
        if report.get(name)!=value:
            raise ValueError('GAMER_G2_0024_PARENT_0023_AGGREGATE_DRIFT:'+name)
    return report,grouped,{'g2_0023_report_sha256':backbone.sha256(report_path),
        'g2_0023_cases_sha256':backbone.sha256(case_path),
        'g2_0023_weights_sha256':backbone.sha256(weight_path)}


def load_parent23_models(directory,guard,expected_sha):
    path=Path(directory)/parent23.WEIGHTS
    if backbone.sha256(path)!=expected_sha:
        raise ValueError('GAMER_G2_0024_PARENT_WEIGHT_HASH')
    tensors=load_file(str(path),device='cpu')
    prototype=parent23.RelationalContextScorer.from_guard(guard)
    expected_keys=set(prototype.state_dict())
    if set(tensors)!={arm+'.'+key for arm in parent23.TRAINED for key in expected_keys}:
        raise ValueError('GAMER_G2_0024_PARENT_WEIGHT_KEYS')
    models={}
    for arm in parent23.TRAINED:
        model=parent23.RelationalContextScorer.from_guard(guard)
        weights={k:tensors[arm+'.'+k] for k in expected_keys}
        if any(weights[k].shape!=v.shape or not torch.isfinite(weights[k]).all()
               for k,v in model.state_dict().items()):
            raise ValueError('GAMER_G2_0024_PARENT_WEIGHT_CONTENT')
        model.load_state_dict(weights,strict=True)
        model.eval().requires_grad_(False)
        models[arm]=model
    return models


def baseline_residual(net,features,base_logits,groups):
    return super(parent23.RelationalContextScorer,net).forward(
        features,base_logits,groups,with_competition=True)


def _audit_forward(net,features,base_logits,groups,variant):
    """Counterfactuals touch ONLY the new residual, never the base scorer.

    `misaligned_peer_values` rotates the value vectors but not their keys,
    within each source/relation group. Deliberately invalid diagnostic data,
    never an inference option. No target labels are read.
    """
    if variant not in VARIANTS:raise ValueError('GAMER_G2_0024_UNKNOWN_VARIANT')
    if variant=='self_only_counterfactual':
        return net(features,base_logits,groups,with_competition=True,contextual=False)
    original=baseline_residual(net,features,base_logits,groups)
    if variant=='residual_disabled' or not groups:return original
    h=net.pair(features);q=net.rel_query(h);k=net.rel_key(h);v=net.rel_value(h)
    chunks=[];start=0
    while start<len(groups):
        stop=start+1
        while stop<len(groups) and groups[stop]==groups[start]:stop+=1
        n=stop-start
        if n>TOP_K:raise ValueError('GAMER_G2_0024_GROUP_EXCEEDS_TOP24')
        this=h[start:stop]
        if n==1:
            chunks.append(torch.zeros(1,device=h.device,dtype=h.dtype))
            start=stop;continue
        if variant=='peer_suppressed':
            ctx=torch.zeros_like(this)
            contrast=torch.zeros((n,1),device=h.device,dtype=h.dtype)
        else:
            delta=torch.tanh((base_logits[start:stop][None,:]-
                              base_logits[start:stop][:,None])/8.)
            score=q[start:stop]@k[start:stop].T/math.sqrt(h.shape[1])
            score=score+net.rel_score_bias*delta
            mask=torch.eye(n,device=h.device,dtype=torch.bool)
            if variant=='uniform_attention':
                weights=(~mask).to(score.dtype)/(n-1)
            else:
                weights=score.masked_fill(mask,torch.finfo(score.dtype).min).softmax(dim=-1)
            peer_values=v[start:stop]
            if variant=='misaligned_peer_values':
                peer_values=peer_values.roll(1,dims=0)
            ctx=weights@peer_values
            contrast=(weights*delta).sum(dim=-1,keepdim=True)
        z=net.rel_transform(torch.cat((this,ctx,ctx-this,contrast),dim=-1))
        chunks.append(net.rel_readout(z).squeeze(-1))
        start=stop
    return original+torch.cat(chunks)


def audit_inventory(net,inv,gold,*,parent_record=None):
    edges=inv['edges'];groups=inv['groups']
    if len(edges)!=len(set(edges)):
        raise ValueError('GAMER_G2_0024_DUPLICATE_INVENTORY')
    with torch.inference_mode():
        scores={variant:_audit_forward(net,inv['features'],inv['base'],groups,variant)
                .detach().cpu() for variant in VARIANTS}
        reference=net(inv['features'],inv['base'],groups,with_competition=True,contextual=True)
        if not torch.allclose(scores['full_context'],reference.detach().cpu(),atol=1e-6,rtol=1e-6):
            raise ValueError('GAMER_G2_0024_FULL_REPLAY_IMPLEMENTATION_DRIFT')
        decisions={a:source13.decode_scores(inv,s,THRESHOLD) for a,s in scores.items()}
        metrics={a:sel.score_set(decisions[a],gold) for a in VARIANTS}
        competition={a:parent22._competition_failures(edges,groups,scores[a],gold)
                     for a in VARIANTS}
        if parent_record is not None:
            old=parent23.TRAINED[1]
            if (parent_record['candidate_count']!=len(edges) or
                parent_record['reachable_gold']!=len(set(edges)&gold) or
                parent_record['configurations'][old]!=metrics['full_context'] or
                parent_record['competition'][old]!=competition['full_context']):
                raise ValueError('GAMER_G2_0024_PARENT_0023_LIVE_REPLAY_DRIFT')
        full=scores['full_context']
        shifts={}
        for a in VARIANTS[1:]:
            delta=(full-scores[a]).abs()
            gained=decisions['full_context']-decisions[a]
            lost=decisions[a]-decisions['full_context']
            shifts[a]={'mean_absolute_logit_shift':float(delta.mean()) if len(delta) else 0.,
                'max_absolute_logit_shift':float(delta.max()) if len(delta) else 0.,
                'scores_shifted_over_1e_4':int((delta>1e-4).sum()),
                'decision_flips':len(gained)+len(lost),
                'full_only_gold':len(gained&gold),
                'counterfactual_only_gold':len(lost&gold),
                'full_only_false_positive':len(gained-gold),
                'counterfactual_only_false_positive':len(lost-gold)}
        sizes=Counter(groups)
        peer_count=sum(1 for group in groups if sizes[group]>1)
        return {'metrics':metrics,'competition':competition,'shifts':shifts,
                'candidates':len(edges),'peer_candidates':peer_count,
                'reachable_gold':len(set(edges)&gold)}


def parameter_utilization(net,guard,seed=parent23.SEED):
    """Reconstruct G2-0023 initial residual tensors without changing RNG state."""
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        prototype=parent23.RelationalContextScorer.from_guard(guard)
    original=prototype.state_dict()
    record={}
    for name,weight in net.state_dict().items():
        if not name.startswith('rel_'):continue
        before=original[name].detach().float().cpu()
        after=weight.detach().float().cpu()
        difference=after-before
        record[name]={'initial_l2':float(torch.linalg.vector_norm(before)),
                      'final_l2':float(torch.linalg.vector_norm(after)),
                      'update_l2':float(torch.linalg.vector_norm(difference)),
                      'update_max_abs':float(difference.abs().max())}
    return record


def gradient_probe(net,features,base_logits,groups):
    """Read-only autograd sensitivity of output logits to learned residual tensors.

    No labels, optimizer, backward accumulation or weight updates. Parameters
    temporarily require gradients solely for autograd.grad and are restored.
    """
    if not any(groups.count(g)>1 for g in groups):return None
    params={name:p for name,p in net.named_parameters() if name.startswith('rel_')}
    if not params:raise ValueError('GAMER_G2_0024_MISSING_RESIDUAL_PARAMETERS')
    state={name:p.requires_grad for name,p in params.items()}
    try:
        for p in params.values():p.requires_grad_(True)
        with torch.enable_grad():
            logits=net(features,base_logits,groups,contextual=True)
            grads=torch.autograd.grad(logits.sum(),list(params.values()),allow_unused=True)
        return {key:{'l2':float(torch.linalg.vector_norm(grad.detach().float())) if grad is not None else 0.,
                     'max_abs':float(grad.detach().abs().max()) if grad is not None else 0.}
                for key,grad in zip(params,grads)}
    finally:
        for name,p in params.items():p.requires_grad_(state[name])


def aggregate(records):
    if not records:raise ValueError('GAMER_G2_0024_EMPTY_AGGREGATE')
    out={'cases':len(records),'candidates':sum(r['candidates'] for r in records),
         'peer_candidates':sum(r['peer_candidates'] for r in records),
         'graph_metrics':{a:base.metrics([{**r['metrics'][a],'family':r['family']}
                          for r in records]) for a in VARIANTS},
         'competition':{a:{key:sum(r['competition'][a][key] for r in records)
                     for key in ('gold_with_competing_negatives','gold_not_ahead_of_hardest_negative')}
                     for a in VARIANTS}}
    for a in VARIANTS[1:]:
        out.setdefault('interventions',{})[a]={
            'mean_absolute_logit_shift':sum(r['shifts'][a]['mean_absolute_logit_shift']*r['candidates']
                                           for r in records)/max(out['candidates'],1),
            'max_absolute_logit_shift':max(r['shifts'][a]['max_absolute_logit_shift'] for r in records),
            **{key:sum(r['shifts'][a][key] for r in records)
               for key in ('scores_shifted_over_1e_4','decision_flips','full_only_gold',
                           'counterfactual_only_gold','full_only_false_positive',
                           'counterfactual_only_false_positive')}}
    return out


def _save_cases(groups,path):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
            for split in SPLITS:
                for row in groups[split]:
                    z.write((json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,competition22_run,
        context23_run,output,device='cuda',max_length=2048):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0024_IMMUTABLE_OUTPUT')
    if max_length!=2048:raise ValueError('GAMER_G2_0024_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0024_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0024_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0024_G1_LINEAGE')
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
        raise ValueError('GAMER_G2_0024_PARENT_0016_LINEAGE')
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
    parent_report22,previous22,h22=parent23._verify_parent22(
        competition22_run,{**fresh21,**fresh22},
        {**h16,**h17,**h18,**h19,**h20,**h21},density.sha_rows(training))
    fresh23=parent23.fresh_cohorts(prior,fresh20,fresh21,fresh22)
    evaluated={**fresh22,**fresh23}
    if len(training)!=640:raise ValueError('GAMER_G2_0024_TRAIN_COUNT_DRIFT')
    all_sets=[training,calibration,independent,withheld,*fresh20.values(),
              *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for rows in all_sets for r in rows})!=sum(map(len,all_sets)):
        raise ValueError('GAMER_G2_0024_TRAIN_DEV_OVERLAP')
    parent_report23,ledger,h23=verify_parent23(context23_run,evaluated,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22},density.sha_rows(training))
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
        parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_state={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0024_BACKBONE_UNFROZEN')
    allowed=sel.compatibility_from_train(original)
    # Capture ONLY the existing G2-0023 new DEV cohorts; not TRAIN or sealed.
    captures={}
    for split,rows in fresh23.items():
        captures[split]=rel.capture(head,backbone_model,tokenizer,rows,
                                    device=device,max_length=max_length,group=split)
        if len(captures[split])!=len(rows):
            raise ValueError('GAMER_G2_0024_CAPTURE_COUNT')
    width=captures[SPLITS[0]][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1]!=width for cases in captures.values() for case in cases):
        raise ValueError('GAMER_G2_0024_WIDTH_DRIFT')
    parent19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
    guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                parent19['new_weights_sha256'])
    controls=load_parent23_models(context23_run,guard,parent_report23['new_weights_sha256'])
    guard_initial={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
    audit_model=controls['relational_context_0023'].to(device)
    capacity=controls['capacity_only_0023'].to(device)
    reference=guard.to(device).eval().requires_grad_(False)
    snapshots={arm:{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
               for arm,net in controls.items()}
    parameter_audit={arm:parameter_utilization(net,reference) for arm,net in controls.items()}
    groups={};gradient_results={}
    for split,rows in fresh23.items():
        records=[];gradient_results[split]=[]
        for index,(case,row) in enumerate(zip(captures[split],rows),1):
            if case['prompt_sha256']!=base._sha(row['prompt']) or case['family']!=row['family']:
                raise ValueError('GAMER_G2_0024_CASE_IDENTITY')
            # rel.capture returns inference tensors. Materialize ordinary tensors
            # outside inference mode so read-only autograd probes can save inputs.
            with torch.no_grad():
                rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
                inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
            inv={'edges':inv['edges'],'groups':inv['groups'],
                 'features':inv['features'].clone().detach().to(device),
                 'base':inv['base'].clone().detach().to(device)}
            result=audit_inventory(audit_model,inv,case['gold'],
                                   parent_record=ledger[split][index-1])
            with torch.inference_mode():
                capacity_scores=capacity(inv['features'],inv['base'],inv['groups'],
                                         with_competition=True,contextual=False)
                capacity_metrics=sel.score_set(source13.decode_scores(inv,capacity_scores,THRESHOLD),
                                               case['gold'])
            if capacity_metrics!=ledger[split][index-1]['configurations']['capacity_only_0023']:
                raise ValueError('GAMER_G2_0024_CAPACITY_PARENT_REPLAY_DRIFT')
            if index in GRADIENT_INDICES or (split==SPLITS[1] and index==128):
                grad=gradient_probe(audit_model,inv['features'],inv['base'],inv['groups'])
                if grad is not None:
                    gradient_results[split].append({'index':index,'gradients':grad})
            records.append({'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
                'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
                'structural_topology':row.get('structural_topology','native'),
                'gold_total':len(case['gold']),**result})
            if index==1 or index%40==0 or index==len(rows):
                print(f'GAMER_G2_0024_AUDIT {split} {index}/{len(rows)}',flush=True)
        groups[split]=records
    rel.validate_original_state(head,head_state)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),v)
           for k,v in guard_initial.items()):
        raise ValueError('GAMER_G2_0024_GUARD_MUTATED')
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0024_BACKBONE_TRAINED')
    for arm,net in controls.items():
        if any(not torch.equal(net.state_dict()[k].detach().cpu(),v)
               for k,v in snapshots[arm].items()):
            raise ValueError('GAMER_G2_0024_CHECKPOINT_MUTATED')
    combined=[r for split in SPLITS for r in groups[split]]
    result={'schema':SCHEMA,'status':'G2_CONTEXT_UTILIZATION_DIAGNOSTIC_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23},
        'datasets_sha256':{k:density.sha_rows(rows) for k,rows in fresh23.items()},
        'predeclared':{'cohorts':list(SPLITS),'fixed_threshold':THRESHOLD,'top_k':TOP_K,
            'variants':list(VARIANTS),'gradient_indices':list(GRADIENT_INDICES),
            'parent_model':'G2-0023 relational_context_0023',
            'counterfactual_only_no_inference_changes':True},
        'parameters':parameter_audit,'gradient_probes':gradient_results,
        'evaluation':aggregate(combined),
        'by_split':{k:aggregate(records) for k,records in groups.items()},
        'by_family':{key:aggregate([r for r in combined if r['family']==key])
                     for key in sorted({r['family'] for r in combined})},
        'by_density':{key:aggregate([r for r in combined if r['density_bucket']==key])
                      for key in sorted({r['density_bucket'] for r in combined})},
        'by_topology':{key:aggregate([r for r in combined if r['structural_topology']==key])
                       for key in sorted({r['structural_topology'] for r in combined})},
        'frozen_checkpoint_sha256':h23['g2_0023_weights_sha256'],
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,
        **_no_promote()}
    output.mkdir(parents=True,exist_ok=False)
    _save_cases(groups,output/CASES)
    result['cases_sha256']=backbone.sha256(output/CASES)
    (output/REPORT).write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+'\n',
                               encoding='utf-8')
    return result
