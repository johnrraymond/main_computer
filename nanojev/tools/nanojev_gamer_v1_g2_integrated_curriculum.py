"""G2-0049: controlled, TRAIN-only learning-method curriculum from G2-0048.

All arms load the exact same immutable occurrence-evidence weights. They share
prompt batches and deterministic retention replay; only rank and cardinality
objectives differ. Transfer and audits are evaluated after fitting, never used
for gradient updates, early stopping, or policy selection. No production route.
"""
from __future__ import annotations

from collections import Counter
import copy
import gzip
import json
import math
import random
import shutil
import tempfile
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48


d47 = d48.d47
d46 = d48.d46
d44 = d48.d44
x33 = d48.x33
g40 = d48.g40
g41 = d48.g41
g42 = d48.g42
b43 = d48.b43
backbone = d48.backbone

SCHEMA = 'nanojev.gamer.graph.g2.integrated-learning-curriculum.v1'
REPORT = 'g2_integrated_curriculum_report.json'
LEDGER = 'g2_integrated_curriculum_per_case.jsonl.gz'
WEIGHTS = 'g2_integrated_curriculum_development.safetensors'
POLICIES = 'g2_integrated_curriculum_frozen_policies.json'
STEPS = 360
BATCH_PROMPTS = 8
EVAL_EVERY = 120
MAX_STEPS = 1200
SEED = 460549
LR = 5e-5
REPLAY_FRACTION = .25
RANK_WEIGHT = .25
COUNT_WEIGHT = .35
TOTAL_WEIGHT = .15
MARGIN = .75
ARMS = ('replay_bce', 'replay_rank', 'replay_count', 'integrated')
SECTIONS = ('learn', 'transfer', 'train_fit', 'natural_train_audit')
EXPECTED_POLICY = {'enabled':False,'config':None,
                   'disabled_reason':'diagnostic_only_no_selection'}


def refuse(code):
    raise ValueError('GAMER_G2_0049_' + code)


def _cohort_name(section):
    return ('train_learn' if section == 'learn' else section) + '_prompts'


def verify_parent48(directory, expected_hashes, parent46, parent47):
    """Read and authenticate G2-0048 artifacts before any training or capture."""
    folder = Path(directory)
    report = json.loads((folder / d48.REPORT).read_text(encoding='utf-8'))
    for key, value in {
        'schema':d48.SCHEMA,
        'status':'TRAIN_OCCURRENCE_EVIDENCE_REPAIR_COMPLETE',
        'source_parent_schema':d47.SCHEMA,
        'parent_hashes':expected_hashes,
        'cohorts':parent46['cohorts'],
        'train_dataset_sha256':parent46['train_dataset_sha256'],
        'training_sha256':parent46['training_sha256'],
        'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,
        'production_promotion':False,
        'g2_certified':False,
        'frozen_g1':True,
        'frozen_g2_0033_parent':True,
        'train_policy':EXPECTED_POLICY,
    }.items():
        if report.get(key) != value:
            refuse('PARENT48_CONTRACT:' + key)
    if report.get('protocol', {}).get('steps') != d48.STEPS or \
       report['protocol'].get('batch_prompts') != d48.BATCH_PROMPTS or \
       not report['protocol'].get('shared_trainable_qwen_window_encoder') or \
       not report['protocol'].get('candidate_inventory_from_g2_0040_unchanged'):
        refuse('PARENT48_PROTOCOL')
    if report.get('reference_frozen_bce',{}).get('transfer') != \
       parent47['results']['arms']['frozen:bce']['transfer']:
        refuse('PARENT48_BASELINE_DRIFT')
    if json.loads((folder/d48.POLICIES).read_text(encoding='utf-8')) != EXPECTED_POLICY:
        refuse('PARENT48_POLICY')
    hashes = {}
    for filename,label in ((d48.REPORT,'report'),(d48.LEDGER,'ledger'),
                           (d48.WEIGHTS,'weights'),(d48.POLICIES,'policies')):
        path=folder/filename
        actual=backbone.sha256(path)
        hashes[f'g2_0048_{label}_sha256']=actual
        if label!='report' and report.get(label+'_sha256') != actual:
            refuse('PARENT48_HASH:' + label)
    observed={section:{} for section in SECTIONS}
    with gzip.open(folder/d48.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            row=json.loads(raw)
            sec=row.get('section');key=row.get('prompt_sha256')
            if sec not in observed or row.get('arm')!='occurrence_evidence' or \
               key not in report['cohorts'][_cohort_name(sec)] or key in observed[sec]:
                refuse('PARENT48_LEDGER_ENTRY')
            if row.get('candidate_reachable') is not (row.get('missing_candidate_gold_edges')==0) or \
               row.get('selected_count',-1)-row.get('gold_count',-1)!= \
               row.get('extra',-1)-row.get('missing',-1) or \
               row.get('exact') is not (row.get('missing')==row.get('extra')==0):
                refuse('PARENT48_LEDGER_ARITHMETIC')
            observed[sec][key]=row
    for sec,rows in observed.items():
        if set(rows)!=set(report['cohorts'][_cohort_name(sec)]):
            refuse('PARENT48_COHORT_MISSING:'+sec)
        actual=d46.summarize(list(rows.values()))
        for group in ('all','candidate_reachable','candidate_unreachable'):
            for field in ('cases','exact','missing','extra','rank_inversions'):
                if actual[group][field]!=report['results'][sec][group][field]:
                    refuse('PARENT48_METRIC_DRIFT:'+sec+':'+group+':'+field)
    return report, hashes, observed


def prepare_count_context(rec, evidence):
    """Frozen, label-blind group/total count priors used at inference."""
    groups=np.asarray(rec['features']['index'], dtype=np.int64)
    edges=rec['shortlisted']['edges']
    n_groups=len(rec['features']['groups'])
    logcounts=torch.as_tensor(rec['structured_pred'][1], dtype=torch.float32).detach().clone()
    total=float(rec['structured_pred'][2])
    if (len(groups)!=len(edges) or len(groups)!=len(evidence['source']) or
        not n_groups or len(groups)==0 or np.min(groups)<0 or np.max(groups)>=n_groups or
        logcounts.shape!=(n_groups,x33.COUNT_CLASSES) or
        not torch.isfinite(logcounts).all() or not math.isfinite(total) or total<=-1.):
        refuse('COUNT_CONTEXT_ALIGNMENT')
    if any((groups==g).sum() == 0 for g in range(n_groups)):
        refuse('EMPTY_COUNT_GROUP')
    return {'group_index':torch.tensor(groups, dtype=torch.long),
            'frozen_logcounts':logcounts,
            'frozen_total':torch.tensor(total,dtype=torch.float32)}


class CurriculumScorer(nn.Module):
    """G2-0048 scorer plus learnable *residual* group/graph count predictions.

    All newly trained count inputs are computed from predicted edge scores and
    frozen count priors; none require gold graph information at inference.
    """
    def __init__(self, width):
        super().__init__()
        self.binding=d48.OccurrenceEvidenceScorer(width)
        features=9
        self.group_count=nn.Sequential(nn.LayerNorm(features),nn.Linear(features,64),
                                       nn.GELU(),nn.Linear(64,x33.COUNT_CLASSES))
        self.total_count=nn.Sequential(nn.LayerNorm(features),nn.Linear(features,48),
                                       nn.GELU(),nn.Linear(48,1))
        nn.init.zeros_(self.group_count[-1].weight)
        nn.init.zeros_(self.group_count[-1].bias)
        nn.init.zeros_(self.total_count[-1].weight)
        nn.init.zeros_(self.total_count[-1].bias)

    def load_baseline(self, weights):
        expected=self.binding.state_dict()
        if set(weights)!=set(expected) or any(
            weights[k].shape!=expected[k].shape or not torch.isfinite(weights[k]).all()
            for k in weights):
            refuse('BASELINE_WEIGHT_SHAPE')
        self.binding.load_state_dict(weights,strict=True)

    def forward(self, evidence, count_context, *, train_counts=False):
        scores=self.binding(evidence)
        idx=count_context['group_index']
        prior=count_context['frozen_logcounts']
        total=count_context['frozen_total']
        if scores.ndim!=1 or len(scores)!=len(idx) or \
           prior.ndim!=2 or prior.shape[-1]!=x33.COUNT_CLASSES or \
           bool((idx>=len(prior)).any()) or bool((idx<0).any()):
            refuse('MODEL_COUNT_ALIGNMENT')
        if not train_counts:
            return scores, prior, total
        n=len(prior)
        lengths=torch.bincount(idx,minlength=n).to(scores.dtype).clamp(min=1)
        confidence=torch.sigmoid(scores)
        def group_average(values):
            sums=torch.zeros(n,device=scores.device,dtype=scores.dtype)
            return sums.index_add(0,idx,values)/lengths
        means=group_average(scores)
        prob=group_average(confidence)
        variance=group_average((scores-means[idx]).square())
        soft_count=group_average(confidence)*lengths
        distribution=prior.softmax(-1)
        expected_prior=(distribution*torch.arange(x33.COUNT_CLASSES,
            device=scores.device,dtype=scores.dtype)[None,:]).sum(-1)
        global_mean=means.mean()
        features=torch.stack((torch.tanh(means/8.),prob,
             torch.tanh(variance/32.),
             torch.log1p(lengths)/math.log1p(x33.MAX_GROUP_EDGES),
             torch.tanh(soft_count/12.),
             expected_prior/x33.MAX_GROUP_EDGES,
             torch.tanh(total/64.).expand_as(means),
             torch.tanh(global_mean/8.).expand_as(means),
             torch.tanh(torch.as_tensor(len(scores)/2048.,dtype=scores.dtype,
                 device=scores.device)).expand_as(means)),dim=1)
        count_logits=F.log_softmax(prior+self.group_count(features),dim=-1)
        # A single global correction conditioned on the entire graph.
        log_total=torch.log1p(total)+self.total_count(features.mean(0)).squeeze(-1)
        # The frozen G2-0033 total can be negative (expm1 of a learned
        # log count). The decoder clips it to zero; preserve that baseline
        # exactly rather than rejecting valid parent predictions.
        total_prediction=torch.expm1(log_total.clamp(-20.,math.log1p(d48.source13.MAX_EDGES)))
        return scores,count_logits,total_prediction


def _on_device(data,device):
    return {key:value.to(device) for key,value in data.items()}


def loss_terms(scores,group_count,total_count,supervision,group_idx,*,
               use_rank=False,use_count=False):
    """Compute BCE, within-group competitive ranking, and set-cardinality losses."""
    _,_,target,contrasts,pos_weight=supervision
    target=target.to(scores.device)
    weight=pos_weight.to(scores.device)
    bce=F.binary_cross_entropy_with_logits(scores,target,pos_weight=weight)
    ranking=scores.sum()*0.
    if use_rank and contrasts:
        ranking=torch.stack([F.softplus(MARGIN+scores[neg.to(scores.device)].max()-
                                scores[pos.to(scores.device)].min())
                             for pos,neg in contrasts]).mean()
    group_loss=scores.sum()*0.
    total_loss=scores.sum()*0.
    if use_count:
        idx=group_idx.to(scores.device)
        group_gold=torch.zeros(len(group_count),device=scores.device,
                               dtype=scores.dtype).index_add(0,idx,target).long()
        if bool((group_gold>=x33.COUNT_CLASSES).any()) or \
           bool((group_gold>torch.bincount(idx,minlength=len(group_count))).any()):
            refuse('COUNT_GOLD_UNREPRESENTABLE')
        group_loss=F.nll_loss(group_count,group_gold)
        total_target=torch.log1p(target.sum())
        total_loss=F.smooth_l1_loss(torch.log1p(total_count.clamp(min=-.999999)),total_target)
    loss=bce+(RANK_WEIGHT*ranking if use_rank else 0.)+(
        COUNT_WEIGHT*group_loss+TOTAL_WEIGHT*total_loss if use_count else 0.)
    if not torch.isfinite(loss):
        refuse('NONFINITE_LOSS')
    return loss,{'bce':float(bce.detach()),'ranking':float(ranking.detach()),
                 'group_count':float(group_loss.detach()),'graph_count':float(total_loss.detach())}


def curriculum_stage(step,steps):
    if not 1<=step<=steps:refuse('BAD_CURRICULUM_STEP')
    if step<=max(1,steps//6):return 'foundation'
    if step<=max(2,steps//2):return 'ranking'
    return 'integration'


def plan_batches(records,steps,batch_size,seed=SEED):
    """Precommitted prompt-ID selection; retention is TRAIN only for every arm."""
    if not records or not 1<=steps<=MAX_STEPS or not 2<=batch_size<=32:
        refuse('BATCH_CONTRACT')
    sha=[d46._sha(r) for r in records]
    if len(set(sha))!=len(sha) or any(r['row'].get('split')!='train' for r in records):
        refuse('TRAIN_PROMPT_INTEGRITY')
    anchors=tuple(i for rank,i in enumerate(sorted(range(len(records)),key=lambda i:sha[i]))
                  if rank%5==0)
    if not anchors:refuse('NO_RETENTION_ANCHORS')
    n_replay=max(1,min(batch_size-1,round(batch_size*REPLAY_FRACTION)))
    schedule=d46.fixed_prompt_batches(len(records),steps,batch_size-n_replay,seed)
    rng=random.Random(seed+2)
    result=[tuple(indices)+tuple(rng.choice(anchors) for _ in range(n_replay))
            for indices in schedule]
    return result,anchors


def score_records(records,net,policy,section,step,evidence,contexts,device,arm):
    rows=[]
    net.eval()
    enable_count=arm in ('replay_count','integrated')
    for rec in records:
        if rec['row'].get('split')!='train':refuse('NONTRAIN_EVALUATION')
        sha=d46._sha(rec)
        with torch.inference_mode():
            scores,counts,total=net(_on_device(evidence[sha],device),
                                     _on_device(contexts[sha],device),
                                     train_counts=enable_count)
            scores_np=scores.double().cpu().numpy()
            counts_np=counts.double().cpu().numpy()
            total_value=float(total.detach().cpu())
        if not np.isfinite(scores_np).all() or not np.isfinite(counts_np).all() or \
           not math.isfinite(total_value):
            refuse('NONFINITE_INFERENCE')
        selected=d44._decode(rec,(scores_np,counts_np,total_value),policy)
        quality=d44._quality(rec,selected,scores_np,section=section,kind=arm,
                            phase='integrated-learning',step=step)
        quality['missing_candidate_gold_edges']=d46._candidate_gap(rec)
        quality['candidate_reachable']=quality['missing_candidate_gold_edges']==0
        rows.append(quality)
    return rows


def train_arm(records,baseline_state,evidence,contexts,supervision,policy,*,arm,
              steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,
              device='cpu',schedule=None):
    if arm not in ARMS or not 3<=steps<=MAX_STEPS or \
       not 2<=batch_size<=32 or not 1<=eval_every<=steps:
        refuse('TRAIN_CONTRACT')
    if set(evidence)!=set(supervision) or set(contexts)!=set(evidence):
        refuse('TRAIN_COHORT_LEAK')
    if any(r['row'].get('split')!='train' for r in records) or \
       set(evidence)!={d46._sha(r) for r in records}:
        refuse('TRAIN_COHORT_LEAK')
    if schedule is None:schedule,anchors=plan_batches(records,steps,batch_size)
    if len(schedule)!=steps or any(len(batch)!=batch_size or
           any(i<0 or i>=len(records) for i in batch) for batch in schedule):
        refuse('BATCH_SCHEDULE_DRIFT')
    torch.manual_seed(SEED)
    width=next(iter(evidence.values()))['windows'].shape[-1]
    model=CurriculumScorer(width)
    model.load_baseline(baseline_state)
    model=model.to(device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=LR,weight_decay=1e-4)
    history=[]
    for step,indices in enumerate(schedule,1):
        model.train();optimizer.zero_grad(set_to_none=True)
        stage=curriculum_stage(step,steps)
        rank=stage!='foundation' and arm in ('replay_rank','integrated')
        count=stage=='integration' and arm in ('replay_count','integrated')
        losses=[];parts=[]
        for i in indices:
            sha=d46._sha(records[i])
            example=_on_device(evidence[sha],device)
            context=_on_device(contexts[sha],device)
            scores,gcount,gtotal=model(example,context,train_counts=count)
            loss,detail=loss_terms(scores,gcount,gtotal,supervision[sha],
                                   context['group_index'],use_rank=rank,use_count=count)
            losses.append(loss);parts.append(detail)
        loss=torch.stack(losses).mean()
        loss.backward()
        grad=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not torch.isfinite(grad) or any(p.grad is not None and not torch.isfinite(p.grad).all()
                                             for p in model.parameters()):
            refuse('NONFINITE_GRADIENT')
        optimizer.step()
        if step==1 or step%eval_every==0 or step==steps:
            rows=score_records(records,model,policy,'learn',step,evidence,contexts,device,arm)
            summary=d46.summarize(rows)
            history.append({'step':step,'stage':stage,'loss':float(loss.detach()),
                'grad_norm':float(grad.detach()),'train_exact':summary['all']['exact'],
                'components':{key:sum(part[key] for part in parts)/len(parts)
                              for key in parts[0]}})
            if step==1 or step%max(eval_every,120)==0 or step==steps:
                print(f'GAMER_G2_0049_FIT {arm} {step}/{steps} stage={stage} '
                      f'exact={summary["all"]["exact"]}/{len(records)} '
                      f'loss={float(loss.detach()):.6f}',flush=True)
    model.eval().requires_grad_(False)
    return model,{'steps_completed':steps,'history':history,
                  'prompt_batches_identical_across_arms':True,
                  'learn_prompts':len(records)}


def paired_outcomes(previous,now):
    by_prompt={row['prompt_sha256']:row for row in now}
    if set(previous)!=set(by_prompt):refuse('PAIRED_COHORT_DRIFT')
    return {'fixed':sum(not previous[k]['exact'] and by_prompt[k]['exact'] for k in previous),
            'regressed':sum(previous[k]['exact'] and not by_prompt[k]['exact'] for k in previous),
            'retained':sum(previous[k]['exact'] and by_prompt[k]['exact'] for k in previous)}


def decision(results,parent48):
    base=parent48['results']['transfer']['candidate_reachable']
    ref=base['exact']
    arms={}
    for name in ARMS:
        cohort=results[name]['transfer']['candidate_reachable']
        learn=results[name]['learn']['candidate_reachable']
        gain=cohort['exact']-ref
        acceptable_edges=(cohort['missing']+cohort['extra']<=base['missing']+base['extra'])
        retention_floor=max(0,parent48['results']['learn']['all']['exact']-5)
        arms[name]={'exact_reachable':cohort['exact'],'gain_vs_0048':gain,
                    'edge_error_not_worse':acceptable_edges,
                    'train_retention_floor_pass':learn['exact']>=retention_floor,
                    'practical_signal':gain>=4 and acceptable_edges and
                                        learn['exact']>=retention_floor}
    candidate=[name for name in ARMS if name!='replay_bce' and arms[name]['practical_signal']]
    causal_gain=any(arms[name]['exact_reachable']-arms['replay_bce']['exact_reachable']>=4
                    for name in candidate)
    return {'reference_0048_reachable_transfer_exact':ref,
            'matched_continuation_reachable_transfer_exact':arms['replay_bce']['exact_reachable'],
            'arms':arms,
            'additional_gain_over_matched_training':causal_gain,
            'decision':('CURRICULUM_TRANSFER_SIGNAL_WITH_MATCHED_CONTROL' if causal_gain else
                'TRAINING_GAIN_ONLY_NO_MATCHED_CAUSAL_SIGNAL' if candidate else
                'NO_DECISIVE_CURRICULUM_TRANSFER_SIGNAL'),
            'promotion_authorized':False}


def stage2(training,fitting,auditing,policy,parent,assert_frozen,output,
           parent45,parent46,parent47,parent48,rows48,baseline_weights,hashes,*,
           steps=STEPS,batch_size=BATCH_PROMPTS,eval_every=EVAL_EVERY,device='cpu'):
    folder=Path(output)
    if folder.exists():raise FileExistsError('GAMER_G2_0049_IMMUTABLE_OUTPUT')
    if not 3<=steps<=MAX_STEPS or not 2<=batch_size<=32 or not 1<=eval_every<=steps:
        refuse('BUDGET')
    cohort=d46.select_cohorts(training,fitting,auditing,parent45,policy)
    d47.verify_cohorts(cohort,parent46)
    if parent48['cohorts']!=parent46['cohorts'] or \
       parent47['cohorts']!=parent46['cohorts']:
        refuse('COHORT_DRIFT')
    sets={key:cohort[key] for key in SECTIONS}
    evidence={};contexts={}
    for section,records in sets.items():
        for rec in records:
            sha=d46._sha(rec)
            if sha in evidence or sha not in rows48[section]:refuse('EVIDENCE_OR_LEDGER_DRIFT')
            ex=d48.prepare_evidence(rec,parent)
            evidence[sha]=ex
            contexts[sha]=prepare_count_context(rec,ex)
    if len(evidence)!=sum(len(section) for section in sets.values()):
        refuse('DUPLICATE_COHORT')
    learn_ids={d46._sha(rec) for rec in sets['learn']}
    supervision=d46.prepare_supervision(sets['learn'],parent)
    if set(supervision)!=learn_ids or any(sha not in evidence for sha in supervision):
        refuse('SUPERVISION_LEAK')
    schedule,anchors=plan_batches(sets['learn'],steps,batch_size)
    learning={sha:evidence[sha] for sha in learn_ids}
    learning_counts={sha:contexts[sha] for sha in learn_ids}
    models={};fit={};results={};ledger=[];retention={}
    frozen_state={k:v.detach().clone() for k,v in parent.state_dict().items()}
    for arm in ARMS:
        model,f=train_arm(sets['learn'],baseline_weights,learning,learning_counts,
            supervision,policy,arm=arm,steps=steps,batch_size=batch_size,
            eval_every=eval_every,device=device,schedule=schedule)
        models[arm]=model
        fit[arm]=f
        results[arm]={}
        retention[arm]={}
        for section,records in sets.items():
            rows=score_records(records,model,policy,section,steps,evidence,contexts,device,arm)
            results[arm][section]=d46.summarize(rows)
            retention[arm][section]=paired_outcomes(rows48[section],rows)
            ledger.extend(rows)
    if any(not torch.equal(value,parent.state_dict()[k]) for k,value in frozen_state.items()):
        refuse('PARENT_G2_0033_MUTATED')
    assert_frozen()
    decision_data=decision(results,parent48)
    report={
        'schema':SCHEMA,'status':'TRAIN_INTEGRATED_CURRICULUM_DIAGNOSTIC_COMPLETE',
        'source_parent_schema':d48.SCHEMA,'parent_hashes':hashes,
        'cohorts':parent46['cohorts'],
        'train_dataset_sha256':parent46['train_dataset_sha256'],
        'training_sha256':parent46['training_sha256'],
        'protocol':{
            'initial_weights':'immutable_g2_0048_occurrence_evidence_development',
            'identical_initial_binding_weights':True,
            'same_batches_steps_and_replay_for_all_arms':True,
            'steps_per_arm':steps,'batch_prompts':batch_size,'eval_every':eval_every,
            'seed':SEED,'optimizer':'AdamW','learning_rate':LR,
            'arms':list(ARMS),
            'phases':['foundation_bce_and_replay',
                      'competitive_ranking_and_replay',
                      'group_graph_count_and_replay'],
            'retention_fraction':REPLAY_FRACTION,
            'retention_anchor_prompt_sha256':[d46._sha(sets['learn'][i]) for i in anchors],
            'contrastive_margin':MARGIN,'contrastive_weight':RANK_WEIGHT,
            'group_count_weight':COUNT_WEIGHT,'total_count_weight':TOTAL_WEIGHT,
            'same_candidate_inventory':True,'frozen_qwen_g1_and_g2_0033':True,
            'predeclared_practical_gain_graphs':4,
            'train_only_labels':True,'no_training_on_transfer_or_audits':True,
            'no_counterfactual_pairs_fabricated':True,
            'fixed_decoder_algorithm':True,
            'new_learned_count_predictions_used_only_in_count_arms':True,
            'immutable_output':True,
        },
        'fit':fit,'results':results,'paired_vs_0048':retention,
        'reference_0048':parent48['results'],
        'decision':decision_data,
        'development_evaluation_skipped':True,
        'dev_labels_used_for_training':False,
        'dev_labels_used_for_calibration':False,
        'sealed_test_used':False,
        'production_promotion':False,'g2_certified':False,
        'frozen_g1':True,'frozen_g2_0033_parent':True,
        'train_policy':EXPECTED_POLICY,
    }
    folder.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix=folder.name+'.staging-',dir=folder.parent))
    try:
        with gzip.open(temp/LEDGER,'wt',encoding='utf-8',compresslevel=6) as stream:
            for row in ledger:stream.write(json.dumps(row,sort_keys=True,allow_nan=False)+'\n')
        weights={f'{arm}.{key}':value.detach().cpu().contiguous().clone()
                 for arm,model in models.items() for key,value in model.state_dict().items()}
        save_file(weights,str(temp/WEIGHTS))
        (temp/POLICIES).write_text(json.dumps(EXPECTED_POLICY,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        report.update({'ledger_sha256':backbone.sha256(temp/LEDGER),
                       'weights_sha256':backbone.sha256(temp/WEIGHTS),
                       'policies_sha256':backbone.sha256(temp/POLICIES)})
        (temp/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        if folder.exists():raise FileExistsError('GAMER_G2_0049_OUTPUT_RACE')
        temp.rename(folder)
    finally:
        if temp.exists():shutil.rmtree(temp)
    return report


def run(*,bakeoff40_run,oracle41_run,rank42_run,absolute43_run,
        memorization44_run,separability45_run,multiprompt46_run,crossover47_run,
        occurrence48_run,output,steps=STEPS,batch_size=BATCH_PROMPTS,
        eval_every=EVAL_EVERY,device='cuda',**kwargs):
    p40,h40=g41.verify_parent40(bakeoff40_run)
    p41,_,h41=g42.verify_parent41(oracle41_run,h40)
    p42,h42=b43.verify_parent42(rank42_run,h40,h41)
    p43,h43=d48.d45.d44.verify_parent43(absolute43_run,h40,h41,h42)
    p44,h44=d48.d45.verify_parent44(memorization44_run,h40,h41,h42,h43)
    p45,h45=d46.verify_parent45(separability45_run,{**h40,**h41,**h42,**h43,**h44})
    p46,h46=d47.verify_parent46(multiprompt46_run,{**h40,**h41,**h42,**h43,**h44,**h45})
    p47,h47=d48.verify_parent47(crossover47_run,
            {**h40,**h41,**h42,**h43,**h44,**h45,**h46},p46)
    p48,h48,rows48=verify_parent48(occurrence48_run,
            {**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47},p46,p47)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0049_IMMUTABLE_OUTPUT')
    weights=load_file(str(Path(occurrence48_run)/d48.WEIGHTS),device='cpu')
    def callback(*,training,fitting,auditing,train_rows,fit_rows,audit_rows,
                 pol33,parent33_models,assert_all_frozen,**ignored):
        for name,rows in (('train_0040',train_rows),('train_fit_0040',fit_rows),
                          ('train_audit_0040',audit_rows)):
            if p46['training_sha256'][name]!=g40.sha_rows(rows):
                refuse('TRAIN_COHORT_DRIFT:'+name)
        policy=pol33[f'structured_set_s{g40.SOURCE_SEED}']
        if policy!=p41['protocol']['config']:
            refuse('DECODER_POLICY_DRIFT')
        return stage2(training,fitting,auditing,policy,
            parent33_models[g40.SOURCE_SEED],assert_all_frozen,output,p45,p46,p47,p48,
            rows48,weights,{**h40,**h41,**h42,**h43,**h44,**h45,**h46,**h47,**h48},
            steps=steps,batch_size=batch_size,eval_every=eval_every,device=device)
    return g40.run(output=output,diagnostic_callback=callback,device=device,**kwargs)
