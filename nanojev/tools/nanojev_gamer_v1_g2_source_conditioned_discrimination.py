"""G2-0013: TRAIN-only source/relation-conditioned multi-positive destination scorer.

A candidate's competing destinations are jointly represented but not forced to
sum to one.  The scorer uses predicted G1 nodes and frozen G2-0004 pair scores.
TRAIN gold is used solely to supervise the new scorer (including hard-null gold)
and for offline DEV calibration/evaluation.  Production stays locked.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import math
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_g2_learned_graph_context as learned8
from tools import nanojev_gamer_v1_g2_recall_preserving_graph as recall9
from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10
from tools import nanojev_gamer_v1_g2_robustness_validation as robust11
from tools import nanojev_gamer_v1_g2_relationship_error_diagnosis as diagnosis12
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES

SCHEMA='nanojev.gamer.graph.g2.source-conditioned-discrimination.v1'
REPORT='g2_source_conditioned_discrimination_report.json'
CASES='g2_source_conditioned_discrimination_per_case.jsonl'
WEIGHTS='g2_source_conditioned_discrimination_development.safetensors'
SEED=430013
TRAIN_LIMIT=rel.TRAIN_LIMIT
CALIBRATION_LIMIT=diagnosis12.LIMIT
VALIDATION_LIMIT=320
TOP_K=24
STEPS=240
LR=3e-4
THRESHOLDS=(.50,.60,.70,.80)
PARENT=robust11.SELECTED
REL_COUNT=len(BIND_CLASSES)-1
EXTRAS=len(BIND_CLASSES)+5


def new_validation_rows(predecessors,*,limit=VALIDATION_LIMIT,seed=SEED):
    if (not predecessors or not all(predecessors) or type(limit) is not int or
        limit<2 or limit%2 or type(seed) is not int):
        raise ValueError('GAMER_G2_0013_BAD_COHORT')
    prior=[row for cohort in predecessors for row in cohort]
    fresh=base.novel_dev_rows(prior,limit=limit,seed=seed)
    hashes=[base._sha(x['prompt']) for x in fresh]
    if (len(hashes)!=len(set(hashes)) or set(hashes)&{base._sha(x['prompt']) for x in prior} or
        Counter(x['family'] for x in fresh)!={'encounter':limit//2,'allocation':limit//2}):
        raise ValueError('GAMER_G2_0013_PROMPT_LEAKAGE')
    return fresh


def verify_diagnosis_parent(directory,rows,expected_hashes):
    """Exact G2-0012 cohort, frozen configuration and case accounting."""
    directory=Path(directory);path=directory/diagnosis12.REPORT;casepath=directory/diagnosis12.CASES
    report=json.loads(path.read_text(encoding='utf-8'))
    required={'schema':diagnosis12.SCHEMA,'status':'G2_RELATIONSHIP_ERROR_DIAGNOSIS_DEV_NOT_CERTIFIED',
              'g1_frozen':True,'g2_0004_rank_weights_frozen':True,'g2_0010_rule_frozen':True,
              'g2_0011_robustness_verified':True,'g2_certified':False,'g2_production_unlocked':False,
              'production_promotion':False,'graph_execution_authorized':False,
              'certification_authorized':False,'sealed_test_used':False,
              'challenge_or_test_split_used':False,'no_training':True,'no_decoder_change':True,
              'new_trainable_parameters':0,'independent_cases':len(rows),
              'dataset_seed':diagnosis12.SEED,'independent_dataset_sha256':density.sha_rows(rows),
              'selected_rule':PARENT,'g1_head_sha256':base.G1_HEAD_SHA,
              'leading_crowded_false_relationship_class':'wrong_destination_same_source_relation'}
    for key,value in required.items():
        if type(report.get(key)) is not type(value) or report[key]!=value:
            raise ValueError('GAMER_G2_0013_PARENT_CONTRACT:'+key)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0013_PARENT_LINEAGE')
    saved=[json.loads(line) for line in casepath.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(saved)!=len(rows):raise ValueError('GAMER_G2_0013_PARENT_CASE_COUNT')
    for index,(row,record) in enumerate(zip(rows,saved),1):
        if (record.get('index')!=index or record.get('family')!=row['family'] or
            record.get('prompt_sha256')!=base._sha(row['prompt']) or
            record.get('gold_relationships')!=len(base.gold_binding_edges(row)) or
            record.get('configurations',{}).keys()!={robust11.PARENT,PARENT}):
            raise ValueError('GAMER_G2_0013_PARENT_CASE_IDENTITY')
    expected_metrics={name:base.metrics([{**r['configurations'][name],'family':r['family']} for r in saved])
                      for name in (robust11.PARENT,PARENT)}
    if expected_metrics!=report.get('independent_metrics') or diagnosis12.aggregate_diagnosis(saved)!=report.get('analysis_by_density'):
        raise ValueError('GAMER_G2_0013_PARENT_METRICS')
    return report,saved,{'g2_0012_report_sha256':backbone.sha256(path),
                         'g2_0012_cases_sha256':backbone.sha256(casepath)}


def make_inventory(case,logits,allowed,*,top_k=TOP_K,train_gold=False):
    """Top destinations per predicted source/relation, including null-argmax pairs.

    Only TRAIN may force-add reachable gold examples for supervision.  DEV and
    inference inventory never use labels.  No single-destination assumption.
    """
    anchors=case['anchors'];n=len(anchors)
    if (type(top_k) is not int or top_k<2 or n<1 or n>128 or len(set(anchors))!=n or
        logits.shape!=(n,n,len(BIND_CLASSES)) or not torch.isfinite(logits).all()):
        raise ValueError('GAMER_G2_0013_BAD_INVENTORY')
    vectors=case['vectors'].detach().float().cpu()
    if vectors.shape[0]!=n or vectors.ndim!=2 or not torch.isfinite(vectors).all():
        raise ValueError('GAMER_G2_0013_BAD_VECTORS')
    probs=logits.detach().float().cpu().softmax(-1)
    margins=logits.detach().float().cpu()-logits.detach().float().cpu()[...,0:1]
    lookup={anchor:i for i,anchor in enumerate(anchors)}
    training_labels=set(case['gold']) if train_gold else set()
    if not train_gold and case.get('force_training_gold'):
        raise ValueError('GAMER_G2_0013_GOLD_IN_INFERENCE')
    groups=[];all_edges=[];inputs=[];all_margins=[];all_null=[];gold_group_count=0
    width=vectors.shape[-1]
    for i,src in enumerate(anchors):
        for k in range(1,len(BIND_CLASSES)):
            relation=BIND_CLASSES[k]
            eligible=[j for j,dst in enumerate(anchors) if i!=j and (relation,src[2],dst[2]) in allowed]
            if not eligible:continue
            eligible.sort(key=lambda j:(-float(margins[i,j,k]),j))
            choices=set(eligible[:top_k])
            if train_gold:
                choices|={lookup[d] for s,d,r in training_labels if s==src and r==relation and d in lookup
                          and lookup[d] in eligible}
            indices=sorted(choices,key=lambda j:(-float(margins[i,j,k]),j))
            if not indices:continue
            group_id=i*REL_COUNT+(k-1)
            maxscore=float(margins[i,indices[0],k]);scale=math.log1p(len(eligible))
            for j in indices:
                dst=anchors[j];edge=(src,dst,relation)
                hot=torch.zeros(len(BIND_CLASSES));hot[k]=1.
                # Raw source/destination vectors and pair interactions are frozen.
                raw=torch.cat((vectors[i],vectors[j],vectors[i]*vectors[j],vectors[i]-vectors[j],hot,
                               torch.tensor([float(probs[i,j,0]),float(probs[i,j,k]),
                                             math.tanh((src[0]-dst[0])/128.),scale,
                                             math.tanh((float(margins[i,j,k])-maxscore)/8.)])))
                inputs.append(raw);all_edges.append(edge);groups.append(group_id)
                all_margins.append(float(margins[i,j,k]));all_null.append(int(probs[i,j].argmax())==0)
            if train_gold and any((src,anchors[j],relation) in training_labels for j in indices):
                gold_group_count+=1
    x=torch.stack(inputs) if inputs else torch.empty((0,4*width+EXTRAS))
    if not torch.isfinite(x).all():raise ValueError('GAMER_G2_0013_BAD_FEATURES')
    return {'features':x,'base':torch.tensor(all_margins),'groups':groups,'edges':all_edges,
            'null_argmax':all_null,'train_positive_groups':gold_group_count}


class SourceConditionedScorer(nn.Module):
    """Per-edge Bernoulli logit conditioned on alternative destination embeddings.

    Attention is used to summarize competitors; *not* to assign exclusive
    probabilities.  All valid destinations can score above the decision cutoff.
    """
    def __init__(self,width,hidden=64):
        super().__init__()
        if type(width) is not int or width<4 or type(hidden) is not int or hidden<16:
            raise ValueError('GAMER_G2_0013_BAD_WIDTH')
        self.width=width
        self.pair=nn.Sequential(nn.Linear(4*width+EXTRAS,hidden),nn.LayerNorm(hidden),nn.GELU())
        self.attention=nn.Linear(hidden,1)
        self.judge=nn.Sequential(nn.Linear(hidden*3+1,hidden),nn.GELU(),nn.Linear(hidden,1))

    def forward(self,features,base,groups,*,with_competition=True):
        if (features.ndim!=2 or features.shape[1]!=4*self.width+EXTRAS or
            base.shape!=(len(features),) or len(groups)!=len(features) or
            not torch.isfinite(features).all() or not torch.isfinite(base).all()):
            raise ValueError('GAMER_G2_0013_BAD_MODEL_INPUT')
        if not len(features):return base
        if any(groups[i]>groups[i+1] for i in range(len(groups)-1)):
            raise ValueError('GAMER_G2_0013_UNSORTED_GROUPS')
        h=self.pair(features)
        if with_competition:
            contexts=[];at=0
            while at<len(groups):
                end=at+1
                while end<len(groups) and groups[end]==groups[at]:end+=1
                chunk=h[at:end]
                if len(chunk)==1:
                    # A candidate must never manufacture its own evidence.
                    contexts.append(torch.zeros_like(chunk))
                else:
                    weights=torch.softmax(self.attention(chunk).squeeze(-1),dim=0)
                    total=(weights[:,None]*chunk).sum(dim=0)
                    # Leave-one-out attention: only competing destinations
                    # contribute to this candidate's source-conditioned view.
                    others=(total[None,:]-weights[:,None]*chunk)/(1.-weights[:,None]).clamp(min=1.e-6)
                    contexts.append(others)
                at=end
            ctx=torch.cat(contexts,dim=0)
        else:ctx=torch.zeros_like(h)
        return self.judge(torch.cat((h,ctx,h-ctx,base.unsqueeze(-1)),dim=-1)).squeeze(-1)


def training_groups(inventory,gold,*,max_groups=12,seed=SEED):
    """TRAIN-only hard-negative groups. Multiple true destinations are retained."""
    by_group=defaultdict(list)
    for index,key in enumerate(inventory['groups']):by_group[key].append(index)
    eligible=[]
    for group,indices in by_group.items():
        positives=[i for i in indices if inventory['edges'][i] in gold]
        negatives=[i for i in indices if inventory['edges'][i] not in gold]
        if not positives or not negatives:continue
        negatives.sort(key=lambda i:(-float(inventory['base'][i]),i))
        # All positive instances (including frozen-null mistakes); strongest
        # wrong destinations plus reproducible random tail.
        chosen=positives+negatives[:min(len(negatives),max(4,4*len(positives)))]
        tail=negatives[max(4,4*len(positives)):]
        rng=random.Random(seed+group)
        chosen+=rng.sample(tail,min(len(tail),max(2,len(positives))))
        eligible.append((group,chosen))
    rng=random.Random(seed)
    return rng.sample(eligible,min(max_groups,len(eligible)))


def train_pair(joint,control,items,*,steps=STEPS,lr=LR,device='cpu',seed=SEED,
               inventory_fn=make_inventory):
    if type(steps) is not int or steps<1 or not math.isfinite(lr) or lr<=0 or not items:
        raise ValueError('GAMER_G2_0013_BAD_TRAIN')
    joint=joint.to(device).train();control=control.to(device).train()
    optim_j=torch.optim.AdamW(joint.parameters(),lr=lr)
    optim_c=torch.optim.AdamW(control.parameters(),lr=lr)
    by_family={name:[i for i,row in enumerate(items) if row['family']==name]
               for name in ('encounter','allocation')}
    if not all(by_family.values()):raise ValueError('GAMER_G2_0013_UNBALANCED_TRAIN')
    rng=random.Random(seed);counts=Counter();last={}
    # A bounded cache prevents holding every crowded graph's pair matrix in RAM.
    @lru_cache(maxsize=8)
    def cached(i):
        item=items[i]
        return inventory_fn(item,item['rank'],item['allowed'],train_gold=True)
    for step in range(steps):
        family=('encounter','allocation')[step%2]
        losses=[[],[]]
        for _ in range(3):
            case=items[rng.choice(by_family[family])]
            inv=cached(case['index']-1)
            sampled=training_groups(inv,case['gold'],max_groups=6,seed=seed+step)
            if not sampled:continue
            indices=sorted({i for _group,group in sampled for i in group},key=lambda i:(inv['groups'][i],i))
            locgroups=[inv['groups'][i] for i in indices]
            x=inv['features'][indices].to(device);b=inv['base'][indices].to(device)
            y=torch.tensor([float(inv['edges'][i] in case['gold']) for i in indices],device=device)
            null=torch.tensor([float(inv['null_argmax'][i]) for i in indices],device=device)
            counts['positive']+=int(y.sum());counts['negative']+=len(y)-int(y.sum())
            counts['hard_null_positive']+=int(((y>0.5)&(null>0.5)).sum())
            for idx,(net,mode) in enumerate(((joint,True),(control,False))):
                pred=net(x,b,locgroups,with_competition=mode)
                weight=torch.where(y>0.5,4.*(1.+null),1.)
                bce=(F.binary_cross_entropy_with_logits(pred,y,reduction='none')*weight).mean()
                rank_losses=[]
                for group in set(locgroups):
                    inds=[j for j,g in enumerate(locgroups) if g==group]
                    positives=pred[inds][y[inds]>0.5];negatives=pred[inds][y[inds]<0.5]
                    if len(positives) and len(negatives):
                        rank_losses.append(F.softplus(.5-positives[:,None]+negatives[None,:]).mean())
                loss=bce+.4*torch.stack(rank_losses).mean() if rank_losses else bce
                losses[idx].append(loss)
        if not all(losses):raise ValueError('GAMER_G2_0013_NO_HARD_NEGATIVE_GROUPS')
        for opt,net,parts,label in ((optim_j,joint,losses[0],'joint'),(optim_c,control,losses[1],'control')):
            opt.zero_grad(set_to_none=True)
            loss=torch.stack(parts).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0013_NONFINITE_LOSS')
            loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.);opt.step()
            last[label]=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_SOURCE_CONDITIONED_TRAIN {step+1}/{steps} joint={last["joint"]:.6f} control={last["control"]:.6f}',flush=True)
    for net in (joint,control):net.eval().requires_grad_(False)
    return {'steps':steps,'last_loss':last,'samples':dict(counts),
            'training_gold_only':True,'matched_control_steps':steps}


def decode_scores(inv,logits,threshold):
    if not 0<threshold<1 or logits.shape!=(len(inv['edges']),):
        raise ValueError('GAMER_G2_0013_BAD_DECODER')
    selected=[(float(score),edge) for edge,score in zip(inv['edges'],logits)
              if float(score)>=math.log(threshold/(1-threshold))]
    # Guard the graph contract without fixed per-source relationship cardinality.
    selected.sort(key=lambda item:(-item[0],item[1]))
    return {edge for _,edge in selected[:MAX_EDGES]}


def evaluation_gate(metrics,buckets,variant):
    parent=metrics[PARENT];candidate=metrics[variant]
    crowded=buckets.get('crowded_49_128',{})
    p=crowded.get(PARENT,{});c=crowded.get(variant,{})
    control=variant.replace('joint_','control_',1)
    return {'global_f1_improves':candidate['f1']>parent['f1'],
            'global_false_positives_decrease':candidate['false_positive']<parent['false_positive'],
            'global_recall_at_least_parent_minus_001':candidate['recall']>=parent['recall']-.01,
            'global_recall_at_least_060':candidate['recall']>=.60,
            'crowded_at_least_30':p.get('cases',0)>=30,
            'crowded_f1_improves':bool(p and c and c['f1']>p['f1']),
            'crowded_recall_at_least_parent_minus_002':bool(p and c and c['recall']>=p['recall']-.02),
            'beats_matched_pair_only_control':candidate['f1']>metrics[control]['f1']}


def choose_rule(metrics,buckets):
    gates={f'joint_{int(t*100):02d}':evaluation_gate(metrics,buckets,f'joint_{int(t*100):02d}')
           for t in THRESHOLDS}
    eligible=[name for name,g in gates.items() if all(g.values())]
    winner=max(eligible,key=lambda name:(metrics[name]['f1'],
                buckets['crowded_49_128'][name]['f1'],metrics[name]['recall'],name)) if eligible else None
    return winner,{'eligible_count':len(eligible),'reason':('MAX_CALIBRATION_F1_WITH_RECALL_AND_CROWDED_GATES'
                if winner else 'NO_SOURCE_CONDITIONED_RULE_PASSES_CALIBRATION')},gates


def evaluate(cases,rank_head,joint,control,allowed,rule,context,retention,*,device,group):
    scores=defaultdict(list);saved=[];upper=Counter()
    names=[PARENT,robust11.PARENT,*[f'{mode}_{int(t*100):02d}' for mode in ('joint','control') for t in THRESHOLDS]]
    joint=joint.to(device).eval();control=control.to(device).eval()
    context=context.to(device).eval();retention=retention.to(device).eval()
    with torch.inference_mode():
        for idx,case in enumerate(cases,1):
            rank=rel.full_logits(rank_head,case['vectors'].to(device)).detach().cpu()
            old=density.candidates(case['anchors'],rank,allowed)
            parent=density.select_edges(old,rule)
            # Reproduce frozen G2-0010 baseline using its exact scoring path.
            feats,odds,edge_order=learned8.graph_features(case['anchors'],case['vectors'],old,parent,rule)
            selected_inds=[i for i,edge in enumerate(edge_order) if edge in parent]
            logit=retention(feats[selected_inds].to(device),odds[selected_inds].to(device),
                            context(feats[selected_inds].to(device),odds[selected_inds].to(device))).detach().cpu()
            selected={edge_order[i] for i,s in zip(selected_inds,logit)
                      if float(s)>=math.log(.40/.60)}
            inventory=make_inventory(case,rank,allowed)
            # Candidate-oracle recall is offline only. No oracle edges enter decode.
            gold=case['gold'];inv_edges=set(inventory['edges']);upper['gold']+=len(gold)
            upper['candidate_gold']+=len(inv_edges&gold)
            upper['parent_gold']+=len(selected&gold)
            variants={robust11.PARENT:parent,PARENT:selected}
            x=inventory['features'].to(device);b=inventory['base'].to(device);g=inventory['groups']
            for mode,net,flag in (('joint',joint,True),('control',control,False)):
                predictions=net(x,b,g,with_competition=flag).detach().cpu()
                for t in THRESHOLDS:variants[f'{mode}_{int(t*100):02d}']=decode_scores(inventory,predictions,t)
            metrics={name:sel.score_set(edges,gold) for name,edges in variants.items()}
            for name,row in metrics.items():scores[name].append({**row,'family':case['family']})
            saved.append({'group':group,'index':idx,'prompt_sha256':case['prompt_sha256'],
                          'family':case['family'],'g1_predicted_nodes':len(case['anchors']),
                          'density_bucket':density._bucket(len(case['anchors'])),
                          'gold_binding_edges':len(gold),'candidate_edges':len(inv_edges),
                          'candidate_gold_offline':len(gold&inv_edges),
                          'frozen_null_gold_in_candidate_offline':sum(1 for edge,null in zip(inventory['edges'],inventory['null_argmax']) if null and edge in gold),
                          'configurations':metrics})
            if idx==1 or idx%40==0 or idx==len(cases):
                print(f'GAMER_G2_0013_EVALUATE {group} {idx}/{len(cases)}',flush=True)
    return {name:base.metrics(rows) for name,rows in scores.items()},saved,dict(upper)


def bucket_metrics(records):
    names=records[0]['configurations']
    return {bucket:{name:base.metrics([{**r['configurations'][name],'family':r['family']}
                                       for r in records if r['density_bucket']==bucket])
                    for name in names} if any(r['density_bucket']==bucket for r in records) else {}
            for bucket in ('small_1_24','medium_25_48','crowded_49_128')}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,
        robustness_run,diagnosis_run,output,device='cuda',max_length=2048,
        validation_limit=VALIDATION_LIMIT,steps=STEPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0013_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(row['prompt']) for row in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0013_TRAIN_DRIFT')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0013_BOOTSTRAP_DRIFT')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=learned8.VALIDATION_LIMIT)
    ninth=recall9.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=recall9.VALIDATION_LIMIT)
    tenth=crowd10.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,limit=crowd10.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    motifs,motif_audit=context7.learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection_dir=Path(selection_run);selection=json.loads((selection_dir/sel.REPORT).read_text())
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0013_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
    _,_,fourth_hashes=density.verify_parent(relational_run,fourth,head,{**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,{**hashes,**disc_hashes,**fourth_hashes})
    _,_,sixth_hashes=context7.verify_graph6_parent(consistency_run,sixth,
                         {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},sel._hash_json(train_evidence))
    _,_,seventh_hashes=learned8.verify_context7_parent(context_run,seventh,
                         {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},sel._hash_json(motif_audit))
    eighth_report,_,eighth_hashes=recall9.verify_learned8_parent(learned_run,eighth,
                         {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,**seventh_hashes})
    prior_hashes={**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,**seventh_hashes,**eighth_hashes}
    ninth_report,_,ninth_hashes=crowd10.verify_recall9_parent(recall_run,eighth,ninth,prior_hashes)
    parent10,parent10_hashes=robust11.verify_crowd10_parent(crowded_run,ninth,tenth,{**prior_hashes,**ninth_hashes})
    if not parent10['selected_independent_screen_pass']:
        raise ValueError('GAMER_G2_0013_UNVALIDATED_G2_0010')
    parent_cohort_limit=json.loads((Path(robustness_run)/robust11.REPORT).read_text()).get('cohort_limit')
    if type(parent_cohort_limit) is not int or parent_cohort_limit!=robust11.COHORT_LIMIT:
        raise ValueError('GAMER_G2_0013_UNSUPPORTED_PARENT_COHORT_SIZE')
    parent_cohorts=robust11.new_cohorts(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,
                                       cohort_count=4,limit=parent_cohort_limit)
    _,parent11_hashes=diagnosis12.verify_robust11_parent(robustness_run,parent_cohorts,
                        {**prior_hashes,**ninth_hashes,**parent10_hashes})
    predecessors=(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,*parent_cohorts)
    twelfth=diagnosis12.fresh_rows(predecessors,limit=CALIBRATION_LIMIT)
    parent12,parent12_records,parent12_hashes=verify_diagnosis_parent(diagnosis_run,twelfth,
                        {**prior_hashes,**ninth_hashes,**parent10_hashes,**parent11_hashes})
    fresh=new_validation_rows((*predecessors,twelfth),limit=validation_limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={name:value.detach().cpu().clone() for name,value in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    g1=deepcopy(head).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):raise ValueError('GAMER_G2_0013_BACKBONE_UNFROZEN')
    train_cache=rel.capture(g1,model,tok,train,device=device,max_length=max_length,group='train')
    calibration=rel.capture(g1,model,tok,twelfth,device=device,max_length=max_length,group='g2_0012_calibration')
    independent=rel.capture(g1,model,tok,fresh,device=device,max_length=max_length,group='new_independent')
    rel.validate_original_state(head,original)
    context,_=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,train_cache[0]['vectors'].shape[1],
                                          eighth_report['context_weights_sha256'])
    retention=crowd10.load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,
                        train_cache[0]['vectors'].shape[1],ninth_report['retention_weights_sha256'])[1]
    width=train_cache[0]['vectors'].shape[1]
    for cache in train_cache:
        cache['rank']=rel.full_logits(head,cache['vectors'].to(device)).detach().cpu()
        cache['allowed']=allowed
    torch.manual_seed(SEED)
    joint=SourceConditionedScorer(width);control=deepcopy(joint)
    training=train_pair(joint,control,train_cache,steps=steps,device=device)
    cal_metrics,cal_records,cal_oracle=evaluate(calibration,head,joint,control,allowed,rule,context,retention,
                                                 device=device,group='previously_inspected_calibration')
    # G2-0012's saved case metrics are the immutable calibration control.
    for record,prior_case in zip(cal_records,parent12_records):
        if any(record['configurations'][name]!=prior_case['configurations'][name]
               for name in (robust11.PARENT,PARENT)):
            raise ValueError('GAMER_G2_0013_PARENT_PREDICTION_DRIFT')
    cal_buckets=bucket_metrics(cal_records)
    chosen,selection,gates=choose_rule(cal_metrics,cal_buckets)
    ind_metrics,ind_records,ind_oracle=evaluate(independent,head,joint,control,allowed,rule,context,retention,
                                                device=device,group='new_independent_validation')
    ind_buckets=bucket_metrics(ind_records)
    screen=evaluation_gate(ind_metrics,ind_buckets,chosen) if chosen else None
    passed=bool(chosen and screen and all(screen.values()))
    rel.validate_original_state(head,original)
    checkpoint={**{'joint.'+k:v.detach().cpu().contiguous() for k,v in joint.state_dict().items()},
                **{'control.'+k:v.detach().cpu().contiguous() for k,v in control.state_dict().items()}}
    report={'schema':SCHEMA,'status':'G2_SOURCE_CONDITIONED_DEV_NOT_CERTIFIED',
            'selected_rule':chosen,'calibration_selection':selection,'calibration_gates':gates,
            'calibration_metrics':cal_metrics,'calibration_by_node_density':cal_buckets,
            'calibration_candidate_oracle':cal_oracle,'calibration_cases':len(calibration),
            'calibration_dataset_sha256':density.sha_rows(twelfth),
            'independent_cases':len(independent),'independent_dataset_sha256':density.sha_rows(fresh),
            'independent_metrics':ind_metrics,'independent_by_node_density':ind_buckets,
            'independent_candidate_oracle':ind_oracle,'independent_selected_gates':screen,
            'independent_screen_pass':passed,'train_info':training,
            'fixed_thresholds_predeclared':list(THRESHOLDS),'candidate_top_k':TOP_K,
            'source_conditioned_multi_positive':True,'hard_negative_same_source_relation':True,
            'frozen_null_gold_explicitly_trained':True,'matched_pair_only_control':True,
            'candidate_expansion_includes_frozen_null_argmax':True,
            'no_one_destination_cap':True,'no_gold_in_inference':True,
            'train_only_gold_candidate_injection':True,
            'g1_frozen':True,'g2_0004_rank_weights_frozen':True,
            'g2_0005_rule_frozen':True,'g2_0010_rule_frozen':True,
            'g2_0011_robustness_verified':True,'g2_0012_diagnosis_verified':True,
            'parent_hashes':{**prior_hashes,**ninth_hashes,**parent10_hashes,**parent11_hashes,**parent12_hashes},
            'g1_development_candidate':base.G1_ARM,'g1_head_sha256':base.G1_HEAD_SHA,
            'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
            'certification_authorized':False,'graph_execution_authorized':False,
            'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False}
    output.mkdir(parents=True,exist_ok=False)
    save_file(checkpoint,str(output/WEIGHTS))
    report['new_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(cal_records+ind_records))
    return report
