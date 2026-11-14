"""G2-0023: TRAIN-only architectural ablation of candidate-specific context.

A capacity-matched *self-only* branch and a multi-positive pairwise-context
branch share G2-0019 initialization, examples, optimizer and base loss.
Previously exposed G2-0022 controls are replayed, never promoted or trained on.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import gzip
import json
import math
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import load_file, save_file

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

SCHEMA = 'nanojev.gamer.graph.g2.contextual-relationship-representation.v1'
REPORT = 'g2_contextual_relationship_representation_report.json'
CASES = 'g2_contextual_relationship_representation_per_case.jsonl.gz'
WEIGHTS = 'g2_contextual_relationship_representation_development.safetensors'
SEED = 460027
EVAL_SEED = 460028
STEPS = parent22.STEPS
LR = parent22.LR
TOP_K = parent22.TOP_K
GROUPS_PER_CASE = parent22.GROUPS_PER_CASE
THRESHOLD = parent22.THRESHOLD
REPLAY = parent22.NEW
NEW = ('new_independent_dev_0023', 'new_topology_dev_0023')
SPLITS = (*REPLAY, *NEW)
ARMS = ('frozen_guard_0019','matched_finetune_0022','competition_aware_0022',
        'capacity_only_0023','relational_context_0023')
TRAINED = ARMS[-2:]


class RelationalContextScorer(source13.SourceConditionedScorer):
    """Candidate-specific pairwise contextual residual over a frozen-schema scorer.

    Each candidate attends to *other destinations in its own source/relation*
    based on its own learned query. Original source-conditioned judgment is
    retained and fine-tuned; an initially-zero residual head avoids unearned
    benefit from random untrained parameters. A self-only arm uses the exact
    same parameter shapes and residual head, but no information from peers in
    the new block. Both preserve independent Bernoulli multi-positive outputs.
    """
    def __init__(self,width,hidden=64):
        super().__init__(width,hidden=hidden)
        self.rel_query=nn.Linear(hidden,hidden,bias=False)
        self.rel_key=nn.Linear(hidden,hidden,bias=False)
        self.rel_value=nn.Linear(hidden,hidden,bias=False)
        self.rel_score_bias=nn.Parameter(torch.zeros(()))
        self.rel_transform=nn.Sequential(nn.Linear(hidden*3+1,hidden),
                                          nn.LayerNorm(hidden),nn.GELU())
        self.rel_readout=nn.Linear(hidden,1)
        nn.init.zeros_(self.rel_readout.weight)
        nn.init.zeros_(self.rel_readout.bias)

    @classmethod
    def from_guard(cls, guard):
        if not isinstance(guard,source13.SourceConditionedScorer):
            raise TypeError('GAMER_G2_0023_UNSUPPORTED_PARENT_SCORER')
        net=cls(guard.width,hidden=guard.pair[0].out_features)
        result=net.load_state_dict(guard.state_dict(),strict=False)
        if result.unexpected_keys or set(result.missing_keys)!={
                k for k in net.state_dict() if k.startswith('rel_')}:
            raise ValueError('GAMER_G2_0023_PARENT_SCORER_DRIFT')
        return net

    def forward(self,features,base,groups,*,with_competition=True,contextual=True):
        # Base path performs strict inventory/group validation and preserves
        # preexisting leave-one-out conditioning for both matched arms.
        original=super().forward(features,base,groups,with_competition=with_competition)
        if not len(groups):return original
        if not with_competition:
            raise ValueError('GAMER_G2_0023_COMPETITION_REQUIRED')
        h=self.pair(features)
        q=self.rel_query(h);k=self.rel_key(h);v=self.rel_value(h)
        residuals=[];start=0
        while start<len(groups):
            end=start+1
            while end<len(groups) and groups[end]==groups[start]:end+=1
            n=end-start
            if n>TOP_K:
                raise ValueError('GAMER_G2_0023_GROUP_EXCEEDS_TOP24')
            chunk=h[start:end]
            if contextual and n>1:
                delta=torch.tanh((base[start:end][None,:]-base[start:end][:,None])/8.)
                similarity=q[start:end]@k[start:end].T/math.sqrt(h.shape[1])
                similarity=similarity+self.rel_score_bias*delta
                mask=torch.eye(n,device=h.device,dtype=torch.bool)
                weights=similarity.masked_fill(mask,torch.finfo(similarity.dtype).min).softmax(dim=-1)
                ctx=weights@v[start:end]
                contrast=(weights*delta).sum(dim=-1,keepdim=True)
            else:
                # Exact same learnable parameterization, but candidate-local
                # only; never use other destinations in the new capacity arm.
                ctx=q[start:end]*(k[start:end]+self.rel_score_bias).sigmoid()+v[start:end]
                contrast=torch.zeros((n,1),dtype=h.dtype,device=h.device)
            z=self.rel_transform(torch.cat((chunk,ctx,ctx-chunk,contrast),dim=-1))
            residual=self.rel_readout(z).squeeze(-1)
            # A singleton has no competitor to supply new relational evidence.
            if n==1:residual=residual*0.
            residuals.append(residual)
            start=end
        return original+torch.cat(residuals)


def fresh_cohorts(history,fresh20,fresh21,fresh22):
    prior=[*history,*(r for rows in (*fresh20.values(),*fresh21.values(),*fresh22.values()) for r in rows)]
    independent=general16.independent_rows((prior,),limit=320,seed=EVAL_SEED)
    topology=general16.heldout_topology_rows((prior,independent),limit=128,seed=EVAL_SEED+1)
    combined=[*prior,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128 or
        len({base._sha(r['prompt']) for r in combined})!=len(combined) or
        {r.get('structural_topology') for r in topology}!={'4x4','4x2','2x4'} or
        any(r.get('split')!='dev' for r in (*independent,*topology))):
        raise ValueError('GAMER_G2_0023_DEV_COHORT_CONTRACT')
    return dict(zip(NEW,(independent,topology)))


def _verify_parent22(directory,rows,hashes,expected_train_hash):
    """Validate complete immutable G2-0022 ledger and both control weights."""
    directory=Path(directory)
    rp,cp,wp=directory/parent22.REPORT,directory/parent22.CASES,directory/parent22.WEIGHTS
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=parent22.SCHEMA:
        raise ValueError('GAMER_G2_0023_PARENT_0022_CONTRACT:schema')
    expected={
        'schema':parent22.SCHEMA,
        'status':'G2_COMPETITION_AWARE_LEARNING_DEV_NOT_CERTIFIED',
        'parent_hashes':hashes,
        'train_dataset_sha256':expected_train_hash,
        'datasets_sha256':{k:density.sha_rows(rows[k]) for k in parent22.SPLITS},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in rows[k]] for k in parent22.NEW},
        'predeclared':{'train_seed':parent22.SEED,'new_dev_seeds':[parent22.EVAL_SEED,parent22.EVAL_SEED+1],
            'steps':parent22.STEPS,'lr':parent22.LR,'top_k':TOP_K,
            'groups_per_case':GROUPS_PER_CASE,'fixed_threshold':THRESHOLD,
            'arms':list(parent22.ARMS),'extra_hard_negative_weight':.75,
            'hard_negative_margin':1.25,'max_hard_negatives_per_group':3},
        'candidate_selection_uses_gold':False,'hard_negative_labels_train_only':True,
        'multi_positive_supervision':True,'same_train_batches':True,
        'same_initial_checkpoint':True,'frozen_g1':True,'frozen_rank_head':True,
        'frozen_backbone':True,'frozen_parent_guard':True,
        'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False,
    }
    for name,value in expected.items():
        if type(report.get(name)) is not type(value) or report[name]!=value:
            raise ValueError('GAMER_G2_0023_PARENT_0022_CONTRACT:'+name)
    if (report.get('cases_sha256')!=backbone.sha256(cp) or
            report.get('new_weights_sha256')!=backbone.sha256(wp)):
        raise ValueError('GAMER_G2_0023_PARENT_0022_HASH_DRIFT')
    for field,value in [('steps',STEPS),('lr',LR),('seed',parent22.SEED),('train_cases',640),
                        ('common_batches',True),('common_initialization',True),
                        ('label_blind_top24',True),('full_selected_groups',True),
                        ('multi_positive_preserved',True)]:
        if report.get('train',{}).get(field)!=value:
            raise ValueError('GAMER_G2_0023_PARENT_0022_TRAIN_DRIFT:'+field)
    grouped={k:[] for k in parent22.SPLITS}
    with gzip.open(cp,'rt',encoding='utf-8') as f:
        for line in f:
            record=json.loads(line)
            split=record.get('group')
            if split not in grouped:
                raise ValueError('GAMER_G2_0023_PARENT_0022_EXTRA_SPLIT')
            i=len(grouped[split]);original=rows[split]
            if i>=len(original):raise ValueError('GAMER_G2_0023_PARENT_0022_CASE_COUNT')
            row=original[i]
            if (record.get('index')!=i+1 or record.get('prompt_sha256')!=base._sha(row['prompt'])
                or record.get('family')!=row['family']
                or record.get('structural_topology')!=row.get('structural_topology','native')
                or record.get('gold_total')!=len(base.gold_binding_edges(row))
                or record.get('configurations',{}).keys()!=set(parent22.ARMS)
                or record.get('competition',{}).keys()!=set(parent22.ARMS)):
                raise ValueError('GAMER_G2_0023_PARENT_0022_LEDGER_DRIFT')
            grouped[split].append(record)
    if any(len(grouped[k])!=len(rows[k]) for k in parent22.SPLITS):
        raise ValueError('GAMER_G2_0023_PARENT_0022_CASE_COUNT')
    cohort={'replayed_0021_dev':[r for s in parent22.REPLAY for r in grouped[s]],
            'new_0022_dev':[r for s in parent22.NEW for r in grouped[s]]}
    checks={
        'by_split':{k:parent22.aggregate(v) for k,v in grouped.items()},
        'evaluation':{k:parent22.aggregate(v) for k,v in cohort.items()},
        'by_family':{k:{f:parent22.aggregate([r for r in data if r['family']==f])
                    for f in ('encounter','allocation') if any(r['family']==f for r in data)}
                    for k,data in cohort.items()},
        'by_density':{k:{d:parent22.aggregate([r for r in data if r['density_bucket']==d])
                     for d in sorted({r['density_bucket'] for r in data})}
                     for k,data in cohort.items()},
        'by_topology':{k:{t:parent22.aggregate([r for r in data if r['structural_topology']==t])
                      for t in ('4x4','4x2','2x4')
                      if any(r['structural_topology']==t for r in data)}
                      for k,data in cohort.items()},
        'diagnostic_gates':{k:parent22.gates(parent22.aggregate(data)) for k,data in cohort.items()},
    }
    for name,value in checks.items():
        if report.get(name)!=value:
            raise ValueError('GAMER_G2_0023_PARENT_0022_AGGREGATE_DRIFT:'+name)
    return report,grouped,{
        'g2_0022_report_sha256':backbone.sha256(rp),
        'g2_0022_cases_sha256':backbone.sha256(cp),
        'g2_0022_weights_sha256':backbone.sha256(wp)}


def _load_parent_controls(directory,guard,expected):
    path=Path(directory)/parent22.WEIGHTS
    if backbone.sha256(path)!=expected:raise ValueError('GAMER_G2_0023_PARENT_WEIGHT_DRIFT')
    tensors=load_file(str(path),device='cpu')
    names=set(guard.state_dict());required={arm+'.'+key for arm in parent22.ARMS[1:] for key in names}
    if set(tensors)!=required:raise ValueError('GAMER_G2_0023_PARENT_WEIGHT_KEYS')
    result={}
    for arm in parent22.ARMS[1:]:
        scorer=deepcopy(guard)
        scorer.load_state_dict({key:tensors[arm+'.'+key] for key in names},strict=True)
        scorer.eval().requires_grad_(False)
        result[arm]=scorer
    return result


def _train(cases,guard,*,steps=STEPS,lr=LR,seed=SEED,device='cpu',
           inventory_fn=source13.make_inventory):
    if (steps!=STEPS or lr!=LR or type(seed) is not int or not cases
        or {r['family'] for r in cases}!={'encounter','allocation'}
        or any(r.get('split')!='train' or r.get('structural_topology') in ('4x4','4x2','2x4')
               or r.get('force_training_gold') for r in cases)):
        raise ValueError('GAMER_G2_0023_FIXED_TRAIN_CONTRACT')
    guard.eval().requires_grad_(False)
    original={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
    torch.manual_seed(seed)
    prototype=RelationalContextScorer.from_guard(guard)
    arms={a:deepcopy(prototype).to(device).train().requires_grad_(True) for a in TRAINED}
    opt={a:torch.optim.AdamW(net.parameters(),lr=lr) for a,net in arms.items()}
    families={f:[i for i,r in enumerate(cases) if r['family']==f] for f in ('encounter','allocation')}
    rng=random.Random(parent22.SEED) # Exactly G2-0022's case/batch stream.
    @lru_cache(maxsize=4)
    def inventory(index):
        case=cases[index]
        out=inventory_fn(case,case['rank'],case['allowed'],top_k=TOP_K,train_gold=False)
        if len(out['edges'])!=len(set(out['edges'])):
            raise ValueError('GAMER_G2_0023_DUPLICATE_CANDIDATES')
        return out
    totals=Counter();last={}
    for step in range(steps):
        batch=[]
        for j in range(3):
            index=rng.choice(families[('encounter','allocation')[(step+j)%2]])
            inv=inventory(index)
            if not inv['edges']:continue
            ids=train19.label_blind_groups(inv,seed=parent22.SEED+step*101+index*7+j,
                                            max_groups=GROUPS_PER_CASE)
            groups=[inv['groups'][i] for i in ids];row=cases[index]
            y=torch.tensor([float(inv['edges'][i] in row['gold']) for i in ids],device=device)
            null=torch.tensor([float(inv['null_argmax'][i]) for i in ids],device=device)
            far=torch.tensor([float(abs(inv['edges'][i][0][0]-inv['edges'][i][1][0])>128)
                              for i in ids],device=device)
            batch.append((inv['features'][ids].to(device),inv['base'][ids].to(device),
                          groups,y,null,far))
            totals['candidates']+=len(ids);totals['positives']+=int(y.sum())
            totals['groups']+=len(set(groups))
        if not batch:raise ValueError('GAMER_G2_0023_EMPTY_BATCH')
        for arm,net in arms.items():
            losses=[]
            for x,b,g,y,null,far in batch:
                logits=net(x,b,g,with_competition=True,contextual=arm==TRAINED[1])
                losses.append(train19.training_objective(logits,y,null,far,g,arm='precision_guard'))
            loss=torch.stack(losses).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0023_NONFINITE_LOSS')
            opt[arm].zero_grad(set_to_none=True);loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
            opt[arm].step();last[arm]=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0023_TRAIN {step+1}/{steps} capacity={last[TRAINED[0]]:.6f} '
                  f'context={last[TRAINED[1]]:.6f}',flush=True)
    for net in arms.values():net.eval().requires_grad_(False)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),v) for k,v in original.items()):
        raise ValueError('GAMER_G2_0023_FROZEN_GUARD_MUTATED')
    return arms,{'steps':steps,'lr':lr,'seed':seed,'batch_seed':parent22.SEED,
                 'counts':dict(totals),'last_train_loss':last,'train_cases':len(cases),
                 'same_initialization':True,'same_training_batches':True,
                 'same_precision_guard_loss':True,'no_new_training_gold_injection':True,
                 'capacity_matched_trainable_parameters':True}


def _evaluate_case(case,row,head,nets,allowed,*,split,index,device,parent_record=None):
    if (case['prompt_sha256']!=base._sha(row['prompt']) or case['family']!=row['family']):
        raise ValueError('GAMER_G2_0023_CASE_IDENTITY')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        if len(inv['edges'])!=len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0023_DUPLICATE_EVAL_CANDIDATES')
        x=inv['features'].to(device);b=inv['base'].to(device)
        score={a:net(x,b,inv['groups'],with_competition=True,
                     **({'contextual':a==TRAINED[1]} if a in TRAINED else {})).detach().cpu()
               for a,net in nets.items()}
        metrics={a:sel.score_set(source13.decode_scores(inv,score[a],THRESHOLD),case['gold'])
                 for a in ARMS}
        failures={a:parent22._competition_failures(inv['edges'],inv['groups'],score[a],case['gold'])
                  for a in ARMS}
        reachable=len(set(inv['edges'])&case['gold'])
        if parent_record is not None:
            if (parent_record.get('prompt_sha256')!=case['prompt_sha256'] or
                parent_record.get('candidate_count')!=len(inv['edges']) or
                parent_record.get('reachable_gold')!=reachable or
                any(parent_record['configurations'][old]!=metrics[new] or
                    parent_record['competition'][old]!=failures[new]
                    for old,new in zip(parent22.ARMS,ARMS[:3]))):
                raise ValueError('GAMER_G2_0023_PARENT_0022_LIVE_REPLAY_DRIFT')
    return {'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
            'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
            'structural_topology':row.get('structural_topology','native'),
            'gold_total':len(case['gold']),'reachable_gold':reachable,
            'candidate_count':len(inv['edges']),
            'configurations':metrics,'competition':failures}


def aggregate(records):
    if not records:raise ValueError('GAMER_G2_0023_EMPTY_METRICS')
    return {'cases':len(records),'metrics':{
        arm:base.metrics([{**r['configurations'][arm],'family':r['family']}
                          for r in records]) for arm in ARMS},
        'competition':{arm:{k:sum(r['competition'][arm][k] for r in records)
                            for k in ('gold_with_competing_negatives',
                                      'gold_not_ahead_of_hardest_negative')}
                       for arm in ARMS},
        'reachable_gold':sum(r['reachable_gold'] for r in records),
        'gold_total':sum(r['gold_total'] for r in records)}


def gates(result):
    m=result['metrics'];c=result['competition'];winner=TRAINED[1];capacity=TRAINED[0]
    return {'context_f1_over_matched_0022':m[winner]['f1']>m[ARMS[1]]['f1'],
            'context_f1_over_capacity_only':m[winner]['f1']>m[capacity]['f1'],
            'context_precision_over_capacity_only':m[winner]['precision']>m[capacity]['precision'],
            'context_recall_within_002_of_matched':m[winner]['recall']>=m[ARMS[1]]['recall']-.02,
            'context_rank_failures_below_capacity_only':
                c[winner]['gold_not_ahead_of_hardest_negative']<
                c[capacity]['gold_not_ahead_of_hardest_negative']}


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
        output,device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0023_IMMUTABLE_OUTPUT')
    if steps!=STEPS or max_length!=2048:
        raise ValueError('GAMER_G2_0023_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0023_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0023_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0023_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,specialization_run=specialization_run)
    training=original+augmented
    report16,rec16,h16=ceiling17.verify_frozen_0016(general_run,source_run,
        routing_robustness_run,augmented,training,calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256'] !=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256']!=parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0023_PARENT_0016_LINEAGE')
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
    parent_report,previous,h22=_verify_parent22(competition22_run,{**fresh21,**fresh22},
                   {**h16,**h17,**h18,**h19,**h20,**h21},density.sha_rows(training))
    newer=fresh_cohorts(prior,fresh20,fresh21,fresh22)
    to_evaluate={**fresh22,**newer}
    if len(training)!=640:raise ValueError('GAMER_G2_0023_TRAIN_COUNT_DRIFT')
    all_sets=[training,calibration,independent,withheld,*fresh20.values(),
              *fresh21.values(),*fresh22.values(),*newer.values()]
    if len({base._sha(r['prompt']) for rows in all_sets for r in rows}) != sum(map(len,all_sets)):
        raise ValueError('GAMER_G2_0023_TRAIN_DEV_OVERLAP')
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
                                     parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
                                                      manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0023_BACKBONE_UNFROZEN')
    allowed=sel.compatibility_from_train(original)
    guard=None;captured={}
    for split,rows in [('train',training),*to_evaluate.items()]:
        captured[split]=rel.capture(head,backbone_model,tokenizer,rows,device=device,
                                    max_length=max_length,group=split)
        if len(captured[split])!=len(rows):
            raise ValueError('GAMER_G2_0023_CAPTURE_COUNT')
        if guard is None:
            width=captured[split][0]['vectors'].shape[1]
            parent19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
            guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                         parent19['new_weights_sha256']).to(device)
            guard_before={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
        if any(c['vectors'].shape[1]!=width for c in captured[split]):
            raise ValueError('GAMER_G2_0023_WIDTH_DRIFT')
    for row,case in zip(training,captured['train']):
        case.update({'rank':rel.full_logits(head,case['vectors'].to(device)).detach().cpu(),
                     'allowed':allowed,'split':row['split'],
                     'structural_topology':row.get('structural_topology','native')})
    parent_models=_load_parent_controls(competition22_run,guard,parent_report['new_weights_sha256'])
    parent_models={key:value.to(device) for key,value in parent_models.items()}
    trained,train_info=_train(captured['train'],guard,steps=steps,device=device)
    if sum(p.numel() for p in trained[TRAINED[0]].parameters())!=sum(
       p.numel() for p in trained[TRAINED[1]].parameters()):
        raise ValueError('GAMER_G2_0023_CAPACITY_MISMATCH')
    nets={ARMS[0]:guard,ARMS[1]:parent_models['matched_finetune'],
          ARMS[2]:parent_models['competition_aware'],**trained}
    groups={}
    for split,rows in to_evaluate.items():
        out=[]
        for i,(case,row) in enumerate(zip(captured[split],rows),1):
            prior_case=previous[split][i-1] if split in REPLAY else None
            out.append(_evaluate_case(case,row,head,nets,allowed,split=split,index=i,
                                      device=device,parent_record=prior_case))
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_0023_EVALUATE {split} {i}/{len(rows)}',flush=True)
        groups[split]=out
    rel.validate_original_state(head,head_before)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),value)
           for k,value in guard_before.items()):
        raise ValueError('GAMER_G2_0023_GUARD_MUTATED')
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0023_BACKBONE_TRAINED')
    cohort={'replayed_0022_dev':[r for split in REPLAY for r in groups[split]],
            'new_0023_dev':[r for split in NEW for r in groups[split]]}
    result={'schema':SCHEMA,'status':'G2_CONTEXTUAL_RELATIONSHIP_DEV_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21,**h22},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(rows) for k,rows in to_evaluate.items()},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in newer[k]] for k in NEW},
        'predeclared':{'train_seed':SEED,'prior_0022_training_batch_seed':parent22.SEED,
            'new_dev_seeds':[EVAL_SEED,EVAL_SEED+1], 'steps':STEPS,'lr':LR,
            'top_k':TOP_K,'groups_per_case':GROUPS_PER_CASE,
            'fixed_threshold':THRESHOLD,'arms':list(ARMS),
            'architecture':'candidate_specific_cross_attention_residual',
            'capacity_control':'same_parameters_candidate_self_only',
            'loss':'g2_0019_precision_guard_unmodified',
            'single_valid_destination_assumption':False},
        'train':train_info,
        'evaluation':{k:aggregate(v) for k,v in cohort.items()},
        'by_split':{k:aggregate(v) for k,v in groups.items()},
        'by_family':{k:{f:aggregate([r for r in rows if r['family']==f])
                        for f in ('encounter','allocation') if any(r['family']==f for r in rows)}
                     for k,rows in cohort.items()},
        'by_density':{k:{b:aggregate([r for r in rows if r['density_bucket']==b])
                         for b in sorted({r['density_bucket'] for r in rows})}
                      for k,rows in cohort.items()},
        'by_topology':{k:{top:aggregate([r for r in rows if r['structural_topology']==top])
                          for top in ('4x4','4x2','2x4')
                          if any(r['structural_topology']==top for r in rows)}
                       for k,rows in cohort.items()},
        'diagnostic_gates':{k:gates(aggregate(rows)) for k,rows in cohort.items()},
        'candidate_selection_uses_gold':False,'gold_used_for_train_loss_only':True,
        'multi_positive_supervision':True,'same_train_batches':True,
        'same_initial_checkpoint':True,'same_loss_for_trained_arms':True,
        'same_trainable_parameter_count':True,
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_parent_guard':True,'frozen_0022_controls':True,
        'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'certification_authorized':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    save_file({a+'.'+key:v.detach().cpu().contiguous() for a,net in trained.items()
               for key,v in net.state_dict().items()},str(output/WEIGHTS))
    result['new_weights_sha256']=backbone.sha256(output/WEIGHTS)
    _save_cases(groups,output/CASES)
    result['cases_sha256']=backbone.sha256(output/CASES)
    (output/REPORT).write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n',
                               encoding='utf-8')
    return result
