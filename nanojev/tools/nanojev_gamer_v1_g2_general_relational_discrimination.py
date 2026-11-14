"""G2-0016: TRAIN-only general relational discrimination under structural transfer.

A single learned source/relation multi-positive scorer must transfer across
predicted-G1 graph sizes. Compare to a same-initialization crowded-only scorer
and the FROZEN G2-0015 route. Evaluation includes fresh familiar-structure DEV
and author-annotated *withheld topology* DEV; never uses challenge/test data.
Nothing in this module promotes a model, changes G1/backbone, or executes graphs.
"""
from __future__ import annotations

from collections import Counter,defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json
import math
import random

import torch
from torch.nn import functional as F
from safetensors.torch import save_file,load_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_corpus as corpus
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
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_density_specialization as specialized14
from tools import nanojev_gamer_v1_g2_density_routing_robustness as robust15

SCHEMA='nanojev.gamer.graph.g2.general-relational-discrimination.v1'
REPORT='g2_general_relational_discrimination_report.json'
CASES='g2_general_relational_discrimination_per_case.jsonl'
WEIGHTS='g2_general_relational_discrimination_development.safetensors'
SEED=460016
TRAIN_EXTRA=480
TRAIN_CROWDED=288
CALIBRATION=160
INDEPENDENT=320
HELDOUT=128
STEPS=320
LR=1.5e-4
THRESHOLDS=(.50,.55,.60,.65,.70,.75,.80)
PARENT=robust15.SELECTED
NAMES=(PARENT,'crowd_only_50','crowd_only_55','crowd_only_60','crowd_only_65',
       'crowd_only_70','crowd_only_75','crowd_only_80',
       'general_50','general_55','general_60','general_65','general_70','general_75','general_80')
TRAIN_TOPOLOGIES=((3,3),(3,4),(4,3))
WITHHELD_TOPOLOGIES=((4,4),(4,2),(2,4))
BUCKETS=(specialized14.BUCKET_SMALL,specialized14.BUCKET_MEDIUM,specialized14.BUCKET_CROWDED)


def reconstruct_predecessors(train):
    """Reproduce the documented prompt exclusions without opening sealed splits."""
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=learned8.VALIDATION_LIMIT)
    ninth=recall9.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=recall9.VALIDATION_LIMIT)
    tenth=crowd10.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,limit=crowd10.VALIDATION_LIMIT)
    eleven=robust11.new_cohorts(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,
                                cohort_count=4,limit=robust11.COHORT_LIMIT)
    previous=(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,tenth,*eleven)
    twelfth=diagnosis12.fresh_rows(previous,limit=diagnosis12.LIMIT)
    thirteenth=source13.new_validation_rows((*previous,twelfth),limit=source13.VALIDATION_LIMIT)
    fourteenth=specialized14.fresh_rows((*previous,twelfth,thirteenth),limit=specialized14.VALIDATION_LIMIT)
    history=(*previous,twelfth,thirteenth,fourteenth)
    fifteen=robust15.new_cohorts(history)
    return (*history,*fifteen),fifteen


def verify_robust15_parent(directory,cohorts,source_run,specialization_run):
    """Fail closed on parent hashes, cohort prompts, scores, gates and weights."""
    directory=Path(directory)
    path=directory/robust15.REPORT;cases=directory/robust15.CASES
    report=json.loads(path.read_text(encoding='utf-8'))
    required={'schema':robust15.SCHEMA,
      'status':'G2_DENSITY_ROUTING_ROBUSTNESS_DEV_NOT_CERTIFIED',
      'selected_route':PARENT,'fallback_rule':robust15.PARENT,
      'robustness_pass':True,'cohort_count':4,'cohort_limit':320,'total_cases':1280,
      'g1_frozen':True,'g2_0004_rank_weights_frozen':True,
      'g2_0010_parent_frozen':True,'g2_0013_joint_weights_frozen':True,
      'g2_0014_selection_frozen':True,'new_trainable_parameters':0,
      'model_selection_on_new_cohorts':False,'small_policy':'joint_60',
      'medium_policy':'fallback','crowded_policy':'fallback',
      'g2_certified':False,'g2_production_unlocked':False,
      'production_promotion':False,'certification_authorized':False,
      'graph_execution_authorized':False,'sealed_test_used':False,
      'challenge_or_test_split_used':False}
    for name,expected in required.items():
        if type(report.get(name)) is not type(expected) or report[name]!=expected:
            raise ValueError('GAMER_G2_0016_PARENT_CONTRACT:'+name)
    if (not all(report.get('robustness_gates',{}).values()) or
        not all(report.get('aggregate_gates',{}).values()) or
        set(report.get('by_cohort',{}))!={'1','2','3','4'} or
        not all(v.get('pass') is True and all(v.get('gates',{}).values())
                for v in report['by_cohort'].values())):
        raise ValueError('GAMER_G2_0016_PARENT_GATES')
    meta=report.get('cohorts',[])
    if len(meta)!=4:raise ValueError('GAMER_G2_0016_PARENT_COHORT_META')
    for i,(rows,info) in enumerate(zip(cohorts,meta),1):
        if (info.get('cohort')!=i or info.get('seed')!=robust15.SEED+104729*(i-1)
            or info.get('cases')!=len(rows) or info.get('dataset_sha256')!=density.sha_rows(rows)):
            raise ValueError('GAMER_G2_0016_PARENT_COHORT_DRIFT')
    parsed=[json.loads(line) for line in cases.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(parsed)!=1280:raise ValueError('GAMER_G2_0016_PARENT_CASE_COUNT')
    for group in range(4):
        subset=parsed[group*320:(group+1)*320]
        robust15._verify_saved_rows(subset,cohorts[group],'new_independent_validation',robust15.NAMES)
        if any(row.get('cohort')!=group+1 for row in subset):
            raise ValueError('GAMER_G2_0016_PARENT_COHORT_ROW')
    replay=robust15.screen(parsed,bootstrap_draws=report['paired_bootstrap']['overall_vs_parent']['draws'])
    normalized=json.loads(json.dumps(replay,sort_keys=True,allow_nan=False))
    if any(report.get(field)!=normalized[field] for field in normalized):
        raise ValueError('GAMER_G2_0016_PARENT_METRIC_DRIFT')
    upstream={
      'g2_0013_report_sha256':Path(source_run)/source13.REPORT,
      'g2_0013_cases_sha256':Path(source_run)/source13.CASES,
      'g2_0013_weights_sha256':Path(source_run)/source13.WEIGHTS,
      'g2_0014_report_sha256':Path(specialization_run)/specialized14.REPORT,
      'g2_0014_cases_sha256':Path(specialization_run)/specialized14.CASES,
      'g2_0014_weights_sha256':Path(specialization_run)/specialized14.WEIGHTS,
    }
    for key,path_item in upstream.items():
        if report.get('parent_hashes',{}).get(key)!=backbone.sha256(path_item):
            raise ValueError('GAMER_G2_0016_PARENT_HASH:'+key)
    parent13=json.loads((Path(source_run)/source13.REPORT).read_text(encoding='utf-8'))
    if parent13.get('new_weights_sha256')!=backbone.sha256(Path(source_run)/source13.WEIGHTS):
        raise ValueError('GAMER_G2_0016_PARENT_SOURCE_WEIGHT')
    return report,{'g2_0015_report_sha256':backbone.sha256(path),
                  'g2_0015_cases_sha256':backbone.sha256(cases)},parent13




def verify_frozen_ancestry15(expected,*,g1_validation_report,g2_run,
                            discrimination_run,relational_run,density_run,
                            consistency_run,context_run,learned_run,recall_run,
                            crowded_run,robustness_run,diagnosis_run,
                            source_run,specialization_run):
    """The full G2-0015 SHA chain must correspond to present disk artifacts."""
    paths={'g1_validation_report_sha256':Path(g1_validation_report)}
    sources=(
        ('g2_bootstrap',g2_run,base,('REPORT','CASES','HEAD')),
        ('g2_0003',discrimination_run,disc,('REPORT','CASES')),
        ('g2_0004',relational_run,rel,('REPORT','CASES','WEIGHTS')),
        ('g2_0005',density_run,density,('REPORT','CASES')),
        ('g2_0006',consistency_run,graph6,('REPORT','CASES')),
        ('g2_0007',context_run,context7,('REPORT','CASES')),
        ('g2_0008',learned_run,learned8,('REPORT','CASES','WEIGHTS')),
        ('g2_0009',recall_run,recall9,('REPORT','CASES','WEIGHTS')),
        ('g2_0010',crowded_run,crowd10,('REPORT','CASES')),
        ('g2_0011',robustness_run,robust11,('REPORT','CASES')),
        ('g2_0012',diagnosis_run,diagnosis12,('REPORT','CASES')),
        ('g2_0013',source_run,source13,('REPORT','CASES','WEIGHTS')),
        ('g2_0014',specialization_run,specialized14,('REPORT','CASES','WEIGHTS')),
    )
    for prefix,directory,module,fields in sources:
        for field in fields:
            suffix='weights' if field in ('HEAD','WEIGHTS') else field.lower()
            key=f'{prefix}_{suffix}_sha256'
            paths[key]=Path(directory)/getattr(module,field)
    if set(paths)!=set(expected):
        raise ValueError('GAMER_G2_0016_PARENT_HASH_KEYSET')
    for key,path in paths.items():
        if expected[key]!=backbone.sha256(path):
            raise ValueError('GAMER_G2_0016_PARENT_ARTIFACT_DRIFT:'+key)
    return True

def load_frozen_rank4(head,path,expected_hash):
    """G2-0004's trained pair scorer, not G2-0001's bootstrap weights."""
    path=Path(path)
    if backbone.sha256(path)!=expected_hash:
        raise ValueError('GAMER_G2_0016_RANK4_WEIGHT_HASH')
    state=load_file(str(path),device='cpu')
    existing=base._binding_snapshot(head)
    if (set(state)!=set(existing) or
        any(state[k].shape!=existing[k].shape or not torch.isfinite(state[k]).all()
            for k in state)):
        raise ValueError('GAMER_G2_0016_RANK4_WEIGHT_SHAPE')
    frozen={k:v.detach().cpu().clone() for k,v in head.state_dict().items() if k not in state}
    head.load_state_dict(state,strict=False)
    rel.validate_original_state(head,frozen)
    return head

def _author_row(rng,split,index,topology):
    if topology=='allocation':
        row=corpus.allocation(rng,split,index)
    else:
        a,b=topology
        names=corpus.take_names(rng,split,a+b)
        row=corpus.encounter(rng,split,index,
            player_override=names[:a],opponent_override=names[a:])
    # This metadata is for diagnostics only; the inference function is never
    # passed topology, graph, split, family, or author labels.
    row['structural_topology']=str(topology) if topology=='allocation' else f'{a}x{b}'
    return row


def curriculum_rows(prior,*,limit=TRAIN_EXTRA,seed=SEED):
    """New author-marked TRAIN rows; OOD topologies are excluded by construction."""
    if type(limit) is not int or limit<60 or limit%15 or type(seed) is not int:
        raise ValueError('GAMER_G2_0016_BAD_TRAIN_CURRICULUM')
    counts=[limit*3//15]*3  # 60% crowded: 3x3,3x4,4x3
    if sum(counts)!=limit*3//5:raise ValueError('GAMER_G2_0016_BAD_CROWD_SPLIT')
    remaining=limit-sum(counts)
    plan=[(topology,counts[i]) for i,topology in enumerate(TRAIN_TOPOLOGIES)]
    plan += [('allocation',remaining//2)]
    for i,top in enumerate(((2,2),(2,3),(3,2))):
        plan.append((top,remaining//6+(1 if i<remaining//2-3*(remaining//6) else 0)))
    rng=random.Random(seed);seen={base._sha(row['prompt']) for group in prior for row in group}
    rows=[];counter=0
    for topology,count in plan:
        for _ in range(count):
            for attempt in range(512):
                row=_author_row(rng,'train',counter+attempt,topology)
                digest=base._sha(row['prompt'])
                if digest not in seen:break
            else:raise ValueError('GAMER_G2_0016_TRAIN_DUPLICATE_EXHAUSTED')
            counter+=1;seen.add(digest);rows.append(row)
    if len(rows)!=limit or any(r['structural_topology'] in ('4x4','4x2','2x4') for r in rows):
        raise ValueError('GAMER_G2_0016_TRAIN_TOPOLOGY_LEAKAGE')
    if len({base._sha(r['prompt']) for r in rows})!=len(rows):
        raise ValueError('GAMER_G2_0016_TRAIN_DUPLICATE')
    return rows


def heldout_topology_rows(prior,*,limit=HELDOUT,seed=SEED+3):
    """DEV-only new structural combinations not present in the TRAIN curriculum."""
    if type(limit) is not int or limit<8 or limit%4 or type(seed) is not int:
        raise ValueError('GAMER_G2_0016_BAD_OOD_CONFIG')
    plan=[((4,4),limit//2),((4,2),limit//4),((2,4),limit//4)]
    seen={base._sha(r['prompt']) for group in prior for r in group}
    rng=random.Random(seed);rows=[]
    for topology,count in plan:
        for i in range(count):
            for attempt in range(512):
                row=_author_row(rng,'dev',100000+len(rows)+attempt,topology)
                digest=base._sha(row['prompt'])
                if digest not in seen:break
            else:raise ValueError('GAMER_G2_0016_OOD_DUPLICATE_EXHAUSTED')
            if row['structural_topology'] not in ('4x4','4x2','2x4'):
                raise ValueError('GAMER_G2_0016_OOD_TOPOLOGY')
            seen.add(digest);rows.append(row)
    return rows


def independent_rows(prior,*,limit,seed):
    if type(limit) is not int or limit<8 or limit%2:
        raise ValueError('GAMER_G2_0016_BAD_DEV_CONFIG')
    old=[row for group in prior for row in group]
    rows=base.novel_dev_rows(old,limit=limit,seed=seed)
    hashes=[base._sha(row['prompt']) for row in rows]
    if len(set(hashes))!=len(rows) or set(hashes)&{base._sha(row['prompt']) for row in old}:
        raise ValueError('GAMER_G2_0016_DEV_OVERLAP')
    return rows


def sample_hard_groups(inv,gold,*,seed,max_groups=6,max_neg=16):
    """TRAIN labels select multi-positive groups and near/far hard negatives."""
    grouped=defaultdict(list)
    for i,g in enumerate(inv['groups']):grouped[g].append(i)
    viable=[];rng=random.Random(seed)
    for g,indices in grouped.items():
        positives=[i for i in indices if inv['edges'][i] in gold]
        negatives=[i for i in indices if inv['edges'][i] not in gold]
        if not positives or not negatives:continue
        negatives.sort(key=lambda i:(-float(inv['base'][i]),i))
        far=[i for i in negatives if abs(inv['edges'][i][0][0]-inv['edges'][i][1][0])>128]
        close=[i for i in negatives if i not in set(far)]
        chosen=set(positives)
        chosen.update(negatives[:min(6,len(negatives))])
        chosen.update(far[:max_neg//3])
        chosen.update(close[:max_neg//3])
        remaining=[i for i in negatives if i not in chosen]
        chosen.update(rng.sample(remaining,min(max_neg//3,len(remaining))))
        viable.append((g,sorted(chosen)))
    rng.shuffle(viable)
    return viable[:max_groups]


def train_general_and_crowd(general,crowd,cases,*,steps=STEPS,lr=LR,seed=SEED,device='cpu'):
    if type(steps) is not int or steps<1 or not math.isfinite(lr) or lr<=0 or not cases:
        raise ValueError('GAMER_G2_0016_BAD_TRAIN')
    crowded=[i for i,item in enumerate(cases)
             if item.get('structural_topology') in ('3x3','3x4','4x3')]
    all_kinds={item['family'] for item in cases}
    if len(crowded)<12 or all_kinds!={'encounter','allocation'}:
        raise ValueError('GAMER_G2_0016_INSUFFICIENT_TRAIN_DIVERSITY')
    for item in cases:
        if item.get('split')!='train' or item.get('structural_topology') in ('4x4','4x2','2x4'):
            raise ValueError('GAMER_G2_0016_NONTRAIN_SUPERVISION')
    rng=random.Random(seed)
    nets=[general.to(device).train(),crowd.to(device).train()]
    opts=[torch.optim.AdamW(net.parameters(),lr=lr) for net in nets]
    @lru_cache(maxsize=6)
    def inventory(i):
        row=cases[i]
        return source13.make_inventory(row,row['rank'],row['allowed'],train_gold=True)
    counts=Counter();last={}
    by_family={family:[i for i,item in enumerate(cases) if item['family']==family]
               for family in ('encounter','allocation')}
    for step in range(steps):
        for arm,(net,opt) in enumerate(zip(nets,opts)):
            losses=[]
            for _ in range(3):
                if arm==0:
                    # General learning spans families and sizes, not a density gate.
                    family=('encounter','allocation')[step%2]
                    idx=rng.choice(by_family[family])
                else:idx=rng.choice(crowded)
                row=cases[idx];inv=inventory(idx)
                groups=sample_hard_groups(inv,row['gold'],seed=seed+step*31+idx)
                if not groups:continue
                ids=sorted({j for _,group in groups for j in group},key=lambda i:(inv['groups'][i],i))
                x=inv['features'][ids].to(device);b=inv['base'][ids].to(device)
                gs=[inv['groups'][i] for i in ids]
                y=torch.tensor([float(inv['edges'][i] in row['gold']) for i in ids],device=device)
                null=torch.tensor([float(inv['null_argmax'][i]) for i in ids],device=device)
                far=torch.tensor([float(abs(inv['edges'][i][0][0]-inv['edges'][i][1][0])>128)
                                  for i in ids],device=device)
                if arm==0:
                    counts['general_hard_negative']+=int((y<.5).sum())
                    counts['general_distant_negative']+=int(((y<.5)&(far>.5)).sum())
                    counts['general_null_positive']+=int(((y>.5)&(null>.5)).sum())
                else:counts['crowd_only_hard_negative']+=int((y<.5).sum())
                logits=net(x,b,gs,with_competition=True)
                # Positives are independent Bernoulli targets. No top-one loss.
                weights=torch.where(y>.5,5.*(1.+null),1.+.75*far)
                bce=(F.binary_cross_entropy_with_logits(logits,y,reduction='none')*weights).mean()
                ranking=[]
                for key in set(gs):
                    members=[j for j,g in enumerate(gs) if g==key]
                    pos=logits[members][y[members]>.5]
                    neg=logits[members][y[members]<.5]
                    if len(pos) and len(neg):
                        ranking.append(F.softplus(.6-pos[:,None]+neg[None,:]).mean())
                losses.append(bce+(.5*torch.stack(ranking).mean() if ranking else 0.))
            if not losses:raise ValueError('GAMER_G2_0016_EMPTY_TRAIN_BATCH')
            opt.zero_grad(set_to_none=True);loss=torch.stack(losses).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0016_NONFINITE_TRAIN')
            loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.);opt.step()
            last['general' if arm==0 else 'crowd_only']=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0016_TRAIN {step+1}/{steps} '
                  f'general={last["general"]:.6f} crowd_only={last["crowd_only"]:.6f}',flush=True)
    for net in nets:net.eval().requires_grad_(False)
    return {'steps':steps,'samples':dict(counts),'last_loss':last,
            'general_training_cases':len(cases),'crowd_only_distinct_cases':len(crowded),
            'gold_only_train_loss':True,'withheld_topology_used_for_training':False}


def metric_groups(records):
    names=records[0]['configurations'];result={}
    for bucket in BUCKETS:
        group=[r for r in records if r['density_bucket']==bucket]
        result[bucket]={name:base.metrics([{**r['configurations'][name],'family':r['family']}
                              for r in group]) for name in names} if group else {}
    return result


def metrics(records):
    return {name:base.metrics([{**r['configurations'][name],'family':r['family']}
                       for r in records]) for name in records[0]['configurations']}


def screen_calibration(all_metrics,buckets):
    """Predeclared gates, selection restricted to calibration labels."""
    parent=all_metrics[PARENT];gates={}
    for name in (n for n in NAMES if n.startswith('general_')):
        m=all_metrics[name]
        g={'global_f1_improves':m['f1']>parent['f1'],
           'global_false_positives_decrease':m['false_positive']<parent['false_positive'],
           'global_recall_at_least_060':m['recall']>=.60,
           'global_recall_loss_le_001':m['recall']>=parent['recall']-.01,
           'outperforms_crowd_only_control':m['f1']>all_metrics[name.replace('general_','crowd_only_')]['f1']}
        for bucket in BUCKETS:
            base_bucket=buckets[bucket].get(PARENT)
            chosen=buckets[bucket].get(name)
            g[bucket+'_represented']=bool(base_bucket and chosen and base_bucket['cases']>=8)
            g[bucket+'_f1_not_worse']=bool(chosen and base_bucket and chosen['f1']>=base_bucket['f1'])
            if bucket==specialized14.BUCKET_CROWDED:
                g['crowded_f1_improves']=bool(chosen and base_bucket and chosen['f1']>base_bucket['f1'])
                g['crowded_recall_loss_le_002']=bool(chosen and base_bucket and
                    chosen['recall']>=base_bucket['recall']-.02)
        gates[name]=g
    eligible=[name for name,checks in gates.items() if all(checks.values())]
    winner=max(eligible,key=lambda name:(all_metrics[name]['f1'],all_metrics[name]['recall'],name)) if eligible else None
    return winner,{'eligible_count':len(eligible),
        'reason':'MAX_CALIBRATION_F1_WITH_CROSS_DENSITY_GATES' if winner else 'NO_GENERAL_POLICY_PASSES_CALIBRATION'},gates


def evaluate(captured,rank_head,joint,context,retention,general,crowd,allowed,rule,
             *,device,group,expected_rows=None):
    """Inference sees predicted G1 nodes and frozen neural outputs, never gold."""
    saved=[]
    for net in (joint,context,retention,general,crowd):net.to(device).eval().requires_grad_(False)
    with torch.inference_mode():
        for index,case in enumerate(captured,1):
            rank=rel.full_logits(rank_head,case['vectors'].to(device)).detach().cpu()
            old=density.candidates(case['anchors'],rank,allowed)
            old5=density.select_edges(old,rule)
            f,o,ordered=learned8.graph_features(case['anchors'],case['vectors'],old,old5,rule)
            ids=[i for i,edge in enumerate(ordered) if edge in old5]
            scores=retention(f[ids].to(device),o[ids].to(device),
                        context(f[ids].to(device),o[ids].to(device))).detach().cpu()
            parent={ordered[i] for i,v in zip(ids,scores) if float(v)>=math.log(.4/.6)}
            bucket=density._bucket(len(case['anchors']))
            if bucket==specialized14.BUCKET_SMALL:
                inv=source13.make_inventory(case,rank,allowed)
                x=inv['features'].to(device);b=inv['base'].to(device)
                parent=source13.decode_scores(inv,joint(x,b,inv['groups'],with_competition=True).cpu(),.60)
            else:inv=source13.make_inventory(case,rank,allowed)
            x=inv['features'].to(device);b=inv['base'].to(device)
            general_logits=general(x,b,inv['groups'],with_competition=True).cpu()
            crowd_logits=crowd(x,b,inv['groups'],with_competition=True).cpu()
            outputs={PARENT:parent}
            for t in THRESHOLDS:
                tag=f'{round(t*100):02d}'
                outputs['general_'+tag]=source13.decode_scores(inv,general_logits,t)
                outputs['crowd_only_'+tag]=source13.decode_scores(inv,crowd_logits,t)
            gold=case['gold'];computed={name:sel.score_set(edges,gold) for name,edges in outputs.items()}
            row={'group':group,'index':index,'family':case['family'],
                 'prompt_sha256':case['prompt_sha256'],
                 'gold_binding_edges':len(gold),
                 'g1_predicted_nodes':len(case['anchors']),'density_bucket':bucket,
                 'structural_topology':(expected_rows[index-1].get('structural_topology','native')
                     if expected_rows else 'native'), 'configurations':computed}
            saved.append(row)
            if index==1 or index%40==0 or index==len(captured):
                print(f'GAMER_G2_0016_EVALUATE {group} {index}/{len(captured)}',flush=True)
    return saved


def validate_records(records,source_rows,group):
    if len(records)!=len(source_rows):raise ValueError('GAMER_G2_0016_CASE_COUNT')
    for i,(record,source) in enumerate(zip(records,source_rows),1):
        if (record.get('group')!=group or record.get('index')!=i or
            record.get('prompt_sha256')!=base._sha(source['prompt']) or
            record.get('family')!=source['family'] or
            record.get('gold_binding_edges')!=len(base.gold_binding_edges(source)) or
            record.get('density_bucket')!=density._bucket(record.get('g1_predicted_nodes',0)) or
            record.get('structural_topology')!=source.get('structural_topology','native') or
            set(record.get('configurations',{}))!=set(NAMES)):
            raise ValueError('GAMER_G2_0016_CASE_IDENTITY')
        for score in record['configurations'].values():
            if (any(type(score.get(k)) is not int or score[k]<0 for k in
                ('correct','missing','false_positive')) or
                type(score.get('exact')) is not bool or
                score['correct']+score['missing']!=record['gold_binding_edges'] or
                score['exact']!=(score['missing']==score['false_positive']==0)):
                raise ValueError('GAMER_G2_0016_CASE_METRIC')


def screen_independent(calibration_choice,independent_metrics,independent_buckets,ood_metrics,ood_by_topology):
    """No threshold selection from independent or OOD cases. Fail closed."""
    if calibration_choice is None:
        return {'calibration_candidate_exists':False,'independent_and_ood_pass':False}
    name=calibration_choice;parent=independent_metrics[PARENT];candidate=independent_metrics[name]
    gates={'calibration_candidate_exists':True,
           'iid_global_f1_improves':candidate['f1']>parent['f1'],
           'iid_false_positives_decrease':candidate['false_positive']<parent['false_positive'],
           'iid_recall_loss_le_001':candidate['recall']>=parent['recall']-.01,
           'iid_recall_at_least_060':candidate['recall']>=.60,
           'iid_beats_crowd_only_control':candidate['f1']>
                independent_metrics[name.replace('general_','crowd_only_')]['f1']}
    for bucket in BUCKETS:
        p=independent_buckets.get(bucket,{}).get(PARENT)
        c=independent_buckets.get(bucket,{}).get(name)
        gates['iid_'+bucket+'_f1_preserved_or_improved']=bool(p and c and c['f1']>=p['f1'])
        if bucket==specialized14.BUCKET_CROWDED:
            gates['iid_crowded_f1_improves']=bool(p and c and c['f1']>p['f1'])
            gates['iid_crowded_recall_loss_le_002']=bool(p and c and c['recall']>=p['recall']-.02)
    oodp=ood_metrics[PARENT];oodc=ood_metrics[name]
    gates['ood_overall_f1_improves']=oodc['f1']>oodp['f1']
    gates['ood_overall_recall_loss_le_001']=oodc['recall']>=oodp['recall']-.01
    gates['ood_beats_crowd_only_control']=(oodc['f1']>
        ood_metrics[name.replace('general_','crowd_only_')]['f1'])
    for topology,table in ood_by_topology.items():
        p=table[PARENT];c=table[name]
        gates['ood_'+topology+'_f1_not_worse']=c['f1']>=p['f1']
        gates['ood_'+topology+'_recall_loss_le_002']=c['recall']>=p['recall']-.02
    gates['independent_and_ood_pass']=all(gates.values())
    return gates


def topology_metrics(records):
    result={}
    for kind in sorted({r['structural_topology'] for r in records}):
        sub=[row for row in records if row['structural_topology']==kind]
        result[kind]=metrics(sub)
    return result


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,learned_run,recall_run,crowded_run,
        robustness_run,diagnosis_run,source_run,specialization_run,routing_robustness_run,output,
        device='cuda',max_length=2048,train_extra=TRAIN_EXTRA,calibration_limit=CALIBRATION,
        independent_limit=INDEPENDENT,heldout_limit=HELDOUT,steps=STEPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0016_IMMUTABLE_OUTPUT')
    if (train_extra!=TRAIN_EXTRA or calibration_limit!=CALIBRATION or
        independent_limit!=INDEPENDENT or heldout_limit!=HELDOUT or steps!=STEPS):
        raise ValueError('GAMER_G2_0016_FIXED_PROTOCOL_REQUIRED')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0016_PARENT_TRAIN_DRIFT')
    predecessors,prior15=reconstruct_predecessors(original)
    if density.sha_rows(predecessors[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0016_PARENT_BOOTSTRAP_DRIFT')
    parent15,parent_hashes,parent13=verify_robust15_parent(routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0016_PARENT_G1_LINEAGE')
    if parent13['g1_head_sha256']!=base.G1_HEAD_SHA:
        raise ValueError('GAMER_G2_0016_PARENT_G1_HEAD')
    verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,
        specialization_run=specialization_run)
    allowed=sel.compatibility_from_train(original)
    # Reuse only the upstream decoder as an immutable baseline. Verify frozen
    # artifacts against their recorded G2-0015 parent hashes before inference.
    upstream=parent15['parent_hashes']
    for key,path in (
        ('g2_0005_report_sha256',Path(density_run)/density.REPORT),
        ('g2_0009_weights_sha256',Path(recall_run)/recall9.WEIGHTS),
        ('g2_0008_weights_sha256',Path(learned_run)/learned8.WEIGHTS)):
        if upstream.get(key)!=backbone.sha256(path):
            raise ValueError('GAMER_G2_0016_FROZEN_UPSTREAM:'+key)
    # G2-0004 fine-tuned the rank/binding tensors; the original bootstrap
    # head does not contain these fitted weights until they are loaded here.
    head=load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
                           upstream['g2_0004_weights_sha256'])
    fixed5=json.loads((Path(density_run)/density.REPORT).read_text(encoding='utf-8'))
    rule=fixed5['fixed_decoder_rules'][graph6.PARENT_ARM]
    augmented=curriculum_rows(predecessors)
    training=original+augmented
    calibration=independent_rows((*predecessors,augmented),limit=calibration_limit,seed=SEED)
    iid=independent_rows((*predecessors,augmented,calibration),limit=independent_limit,seed=SEED+1)
    heldout=heldout_topology_rows((*predecessors,augmented,calibration,iid),limit=heldout_limit)
    all_rows=(*training,*calibration,*iid,*heldout)
    if len({base._sha(r['prompt']) for r in all_rows})!=len(all_rows):
        raise ValueError('GAMER_G2_0016_SPLIT_LEAKAGE')
    originals={key:v.detach().cpu().clone() for key,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    capture_head=deepcopy(head).to(device).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0016_BACKBONE_UNFROZEN')
    captured=[]
    for label,rows in (('general_train',training),('calibration',calibration),
                       ('new_independent',iid),('withheld_topology',heldout)):
        batch=rel.capture(capture_head,model,tok,rows,device=device,max_length=max_length,group=label)
        captured.append(batch)
    width=captured[0][0]['vectors'].shape[1]
    if any(item['vectors'].shape[1]!=width for group in captured for item in group):
        raise ValueError('GAMER_G2_0016_FEATURE_WIDTH_DRIFT')
    for row,case in zip(training,captured[0]):
        case.update({'rank':rel.full_logits(head,case['vectors'].to(device)).detach().cpu(),
                     'allowed':allowed,'structural_topology':row.get('structural_topology','native'),
                     'split':row['split']})
    source_models=specialized14.load_source13_models(Path(source_run)/source13.WEIGHTS,
                                                   width,parent13['new_weights_sha256'])
    init=source_models['joint'].state_dict()
    torch.manual_seed(SEED)
    if torch.cuda.is_available():torch.cuda.manual_seed_all(SEED)
    general=source13.SourceConditionedScorer(width)
    crowd=source13.SourceConditionedScorer(width)
    general.load_state_dict(init);crowd.load_state_dict(init)
    stats=train_general_and_crowd(general,crowd,captured[0],steps=steps,device=device)
    context,_=recall9.load_frozen_scorers(Path(learned_run)/learned8.WEIGHTS,width,
                   json.loads((Path(learned_run)/learned8.REPORT).read_text())['context_weights_sha256'])
    retention=crowd10.load_frozen_retention(Path(recall_run)/recall9.WEIGHTS,width,
                   json.loads((Path(recall_run)/recall9.REPORT).read_text())['retention_weights_sha256'])[1]
    results=[]
    for group,rows,cases in zip(('calibration','new_independent_validation','withheld_topology_validation'),
                                (calibration,iid,heldout),captured[1:]):
        part=evaluate(cases,head,source_models['joint'],context,retention,general,crowd,
                    allowed,rule,device=device,group=group,expected_rows=rows)
        validate_records(part,rows,group);results.append(part)
    rel.validate_original_state(head,originals)
    by_group={name:{'cases':len(part),'dataset_sha256':density.sha_rows(rows),
                    'metrics':metrics(part),'by_node_density':metric_groups(part)}
              for name,part,rows in zip(('calibration','new_independent_validation','withheld_topology_validation'),
                                        results,(calibration,iid,heldout))}
    winner,decision,gates=screen_calibration(by_group['calibration']['metrics'],
                                         by_group['calibration']['by_node_density'])
    ood_top=topology_metrics(results[2])
    independent_gates=screen_independent(winner,by_group['new_independent_validation']['metrics'],
                  by_group['new_independent_validation']['by_node_density'],
                  by_group['withheld_topology_validation']['metrics'],ood_top)
    reported_hashes={**parent_hashes,
       'g2_0013_weights_sha256':parent13['new_weights_sha256'],
       'g2_0014_report_sha256':upstream['g2_0014_report_sha256']}
    report={'schema':SCHEMA,'status':'G2_GENERAL_RELATIONAL_DISCRIMINATION_DEV_NOT_CERTIFIED',
            'parent_configuration':PARENT,'train_extra_cases':len(augmented),
            'train_total_cases':len(training),
            'train_crowded_augmentation_cases':sum(r['structural_topology'] in ('3x3','3x4','4x3') for r in augmented),
            'train_topology_counts':dict(Counter(r['structural_topology'] for r in augmented)),
            'train_dataset_sha256':density.sha_rows(training),
            'training':stats,'thresholds_predeclared':list(THRESHOLDS),
            'parent_0015_robustness_verified':True,'parent_hashes':reported_hashes,
            'frozen_source13_sha256':parent13['new_weights_sha256'],
            'calibration_selection':{'name':winner,**decision},'calibration_gates':gates,
            'evaluation':by_group,'withheld_topology_metrics':ood_top,
            'independent_acceptance_gates':independent_gates,
            'independent_acceptance_pass':independent_gates['independent_and_ood_pass'],
            'heldout_topologies':[f'{a}x{b}' for a,b in WITHHELD_TOPOLOGIES],
            'g1_frozen':True,'rank_head_frozen':True,'g2_0015_route_frozen':True,
            'new_trainable_models':['general','crowd_only'],
            'no_density_specific_inference_router':True,
            'inherited_candidate_set_size_feature':True,
            'no_topology_gold_or_family_input_to_inference':True,
            'training_gold_only':True,'calibration_only_model_selection':True,
            'independent_and_ood_not_used_for_selection':True,
            'novel_topologies_within_original_encounter_generator_only':True,
            'cross_domain_generality_not_claimed':True,
            'sealed_test_used':False,'challenge_or_test_split_used':False,
            'g2_certified':False,'g2_production_unlocked':False,
            'production_promotion':False,'graph_execution_authorized':False,
            'certification_authorized':False,'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    weight_file=output/WEIGHTS
    save_file({prefix+'.'+k:v.detach().cpu().contiguous()
               for prefix,net in (('general',general),('crowd_only',crowd))
               for k,v in net.state_dict().items()},str(weight_file))
    report['new_weights_sha256']=backbone.sha256(weight_file)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl([r for part in results for r in part]))
    return report
