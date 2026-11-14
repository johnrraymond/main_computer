"""G2-0022: matched, TRAIN-only competition-aware relational fine-tuning.

Both arms start from the immutable G2-0019 precision-guard branch and see the
same *label-blind* full source/relation top-24 groups. The experimental arm
adds within-group hard-negative supervision. Each positive remains independent;
no softmax or single-destination assumption is introduced. Everything is DEV.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import gzip
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
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as ceiling21

SCHEMA = 'nanojev.gamer.graph.g2.competition-aware-learning.v1'
REPORT = 'g2_competition_aware_learning_report.json'
CASES = 'g2_competition_aware_learning_per_case.jsonl.gz'
WEIGHTS = 'g2_competition_aware_learning_development.safetensors'
SEED = 460024
EVAL_SEED = 460025
STEPS = 320
LR = 6.e-5
THRESHOLD = train19.THRESHOLD
TOP_K = 24
GROUPS_PER_CASE = train19.GROUPS_PER_CASE
ARMS = ('frozen_guard_0019', 'matched_finetune', 'competition_aware')
NEW = ('new_independent_dev_0022', 'new_topology_dev_0022')
REPLAY = ceiling21.ASSESS
SPLITS = (*REPLAY, *NEW)


def _read_parent21(directory, rows, hashes):
    """Fail closed on parent identity, compressed ledger, and no-promotion flags."""
    directory = Path(directory)
    rp, cp, fp = (directory/ceiling21.REPORT, directory/ceiling21.CASES,
                  directory/ceiling21.FRONTIER)
    report = json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=ceiling21.SCHEMA:
        raise ValueError('GAMER_G2_0022_PARENT_0021_CONTRACT:schema')
    if set(rows)!=set(ceiling21.SPLITS):
        raise ValueError('GAMER_G2_0022_PARENT_0021_SPLIT_DRIFT')
    required = {
        'schema':ceiling21.SCHEMA,
        'status':'G2_DISCRIMINATION_CEILING_DEV_NOT_CERTIFIED',
        'parent_hashes':hashes,
        'datasets_sha256':{s:density.sha_rows(rows[s]) for s in ceiling21.SPLITS},
        'predeclared':{
            'top_k':TOP_K, 'fixed_threshold':THRESHOLD,
            'new_seed_independent':ceiling21.SEED,
            'new_seed_topology':ceiling21.SEED+1,
            'selection_cohorts':list(ceiling21.SELECT),
            'assessment_cohorts':list(ceiling21.ASSESS),
            'min_stratum_cases':ceiling21.MIN_STRATUM_CASES,
            'policies':list(ceiling21.POLICIES),
            'max_graph_edges':ceiling21.MAX_EDGES,
        },
        'new_training':False, 'new_trainable_parameters':0,
        'weights_mutated':False, 'model_selection':False,
        'checkpoint_selection':False, 'production_threshold_selection':False,
        'label_blind_candidate_inventory':True,
        'new_cohorts_are_dev_not_sealed':True,
        'sealed_test_used':False, 'challenge_or_test_split_used':False,
        'certification_authorized':False, 'g2_certified':False,
        'g2_production_unlocked':False, 'production_promotion':False,
        'graph_execution_authorized':False, 'memory_writes':False,
    }
    for field,value in required.items():
        if type(report.get(field)) is not type(value) or report[field]!=value:
            raise ValueError('GAMER_G2_0022_PARENT_0021_CONTRACT:'+field)
    if (report.get('cases_sha256')!=backbone.sha256(cp) or
            report.get('frontier_sha256')!=backbone.sha256(fp)):
        raise ValueError('GAMER_G2_0022_PARENT_0021_SHA_DRIFT')
    for split in ceiling21.ASSESS:
        if report.get('new_prompt_sha256',{}).get(split) != [base._sha(r['prompt']) for r in rows[split]]:
            raise ValueError('GAMER_G2_0022_PARENT_0021_PROMPT_DRIFT')
    counts=Counter();replay={k:[] for k in REPLAY}
    with gzip.open(cp,'rt',encoding='utf-8') as f:
        for line in f:
            item=json.loads(line)
            split=item.get('group')
            if split not in rows:
                raise ValueError('GAMER_G2_0022_PARENT_0021_UNEXPECTED_SPLIT')
            counts[split]+=1
            i=counts[split]
            if i>len(rows[split]):raise ValueError('GAMER_G2_0022_PARENT_0021_EXTRA_ROW')
            row=rows[split][i-1]
            if (item.get('index')!=i or item.get('prompt_sha256')!=base._sha(row['prompt'])
                or item.get('family')!=row['family']
                or item.get('structural_topology')!=row.get('structural_topology','native')
                or item.get('gold_total')!=len(base.gold_binding_edges(row))):
                # Do not silently tolerate changed labels or dataset identities.
                raise ValueError('GAMER_G2_0022_PARENT_0021_LEDGER_DRIFT')
            if split in replay:replay[split].append(item)
    if dict(counts)!={k:len(v) for k,v in rows.items()}:
        raise ValueError('GAMER_G2_0022_PARENT_0021_COUNT_DRIFT')
    return report,replay,{
        'g2_0021_report_sha256':backbone.sha256(rp),
        'g2_0021_cases_sha256':backbone.sha256(cp),
        'g2_0021_frontier_sha256':backbone.sha256(fp),
    }


def fresh_cohorts(history, fresh20, fresh21):
    seen=[*history,*fresh20[ceiling20.FRESH[0]],*fresh20[ceiling20.FRESH[1]],
          *fresh21[ceiling21.ASSESS[0]],*fresh21[ceiling21.ASSESS[1]]]
    independent=general16.independent_rows((seen,),limit=320,seed=EVAL_SEED)
    topology=general16.heldout_topology_rows((seen,independent),limit=128,
                                               seed=EVAL_SEED+1)
    combined=[*seen,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128
        or len({base._sha(r['prompt']) for r in combined})!=len(combined)
        or {r.get('structural_topology') for r in topology}!={'4x4','4x2','2x4'}
        or any(r.get('split')!='dev' for r in (*independent,*topology))):
        raise ValueError('GAMER_G2_0022_COHORT_CONTRACT')
    return dict(zip(NEW,(independent,topology)))


def local_hard_negative_loss(logits, targets, groups, *, margin=1.25, top_hard=3):
    """Multi-positive within-source/relation ranking, never exclusive softmax.

    Hard negatives are selected using TRAIN labels after label-blind candidate
    generation; *every positive* gets the same opportunity to outrank every
    selected hard negative. A negative competes only with its own source/relation.
    """
    if (logits.ndim!=1 or logits.shape!=targets.shape or len(groups)!=len(logits)
        or not logits.numel() or top_hard<1 or not math.isfinite(margin)
        or not torch.isfinite(logits).all() or not torch.isfinite(targets).all()
        or bool(((targets!=0)&(targets!=1)).any())):
        raise ValueError('GAMER_G2_0022_BAD_RANKING_INPUT')
    grouped=defaultdict(list)
    for i,g in enumerate(groups):grouped[g].append(i)
    losses=[];pairs=0;eligible_groups=0
    for indices in grouped.values():
        t=targets[indices]
        pos=logits[indices][t>.5]
        neg=logits[indices][t<.5]
        if not pos.numel() or not neg.numel():continue
        hard=neg.topk(min(neg.numel(),top_hard)).values
        losses.append(F.softplus(margin+hard[None,:]-pos[:,None]).mean())
        pairs+=pos.numel()*hard.numel();eligible_groups+=1
    return ((torch.stack(losses).mean() if losses else logits.sum()*0.),
            {'ranked_groups':eligible_groups,'hard_pairs':int(pairs)})


def joint_objective(logits,targets,null,far,groups,*,arm):
    if arm not in ARMS[1:]:raise ValueError('GAMER_G2_0022_UNKNOWN_ARM')
    common=train19.training_objective(logits,targets,null,far,groups,arm='precision_guard')
    if arm=='matched_finetune':return common
    hard,_=local_hard_negative_loss(logits,targets,groups)
    # Preserve per-edge Bernoulli recall supervision already in common loss.
    return common+0.75*hard


def train(cases,frozen,*,steps=STEPS,lr=LR,seed=SEED,device='cpu',
          inventory_fn=source13.make_inventory):
    if (steps!=STEPS or lr!=LR or type(seed) is not int or not cases or
        {r['family'] for r in cases}!={'encounter','allocation'}):
        raise ValueError('GAMER_G2_0022_FIXED_TRAIN_CONTRACT')
    if any(r.get('split')!='train' or r.get('structural_topology') in ('4x4','4x2','2x4')
           or r.get('force_training_gold') for r in cases):
        raise ValueError('GAMER_G2_0022_NONTRAIN_LABEL')
    frozen.eval().requires_grad_(False)
    original={k:v.detach().cpu().clone() for k,v in frozen.state_dict().items()}
    torch.manual_seed(seed)
    models={arm:deepcopy(frozen).to(device).train().requires_grad_(True) for arm in ARMS[1:]}
    optim={arm:torch.optim.AdamW(net.parameters(),lr=lr) for arm,net in models.items()}
    families={f:[i for i,row in enumerate(cases) if row['family']==f] for f in ('encounter','allocation')}
    rng=random.Random(seed)
    @lru_cache(maxsize=4)
    def inventory(index):
        case=cases[index]
        inv=inventory_fn(case,case['rank'],case['allowed'],top_k=TOP_K,train_gold=False)
        if len(inv['edges'])!=len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0022_DUPLICATE_CANDIDATES')
        return inv
    totals=Counter();last={}
    for step in range(steps):
        batch=[]
        for j in range(3):
            index=rng.choice(families[('encounter','allocation')[(step+j)%2]])
            inv=inventory(index)
            if not inv['edges']:continue
            ids=train19.label_blind_groups(inv,seed=seed+step*101+index*7+j,
                                            max_groups=GROUPS_PER_CASE)
            row=cases[index];group=[inv['groups'][i] for i in ids]
            target=torch.tensor([float(inv['edges'][i] in row['gold']) for i in ids],device=device)
            null=torch.tensor([float(inv['null_argmax'][i]) for i in ids],device=device)
            far=torch.tensor([float(abs(inv['edges'][i][0][0]-inv['edges'][i][1][0])>128)
                              for i in ids],device=device)
            totals['candidates']+=len(ids)
            totals['positives']+=int(target.sum());totals['groups']+=len(set(group))
            batch.append((inv['features'][ids].to(device),inv['base'][ids].to(device),
                          group,target,null,far))
        if not batch:raise ValueError('GAMER_G2_0022_EMPTY_BATCH')
        for arm,net in models.items():
            values=[]
            for x,b,g,y,null,far in batch:
                logits=net(x,b,g,with_competition=True)
                values.append(joint_objective(logits,y,null,far,g,arm=arm))
            loss=torch.stack(values).mean()
            if not torch.isfinite(loss):raise ValueError('GAMER_G2_0022_NONFINITE_LOSS')
            optim[arm].zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
            optim[arm].step()
            last[arm]=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_0022_TRAIN {step+1}/{steps} matched={last["matched_finetune"]:.6f} '
                  f'competition={last["competition_aware"]:.6f}',flush=True)
    for model in models.values():model.eval().requires_grad_(False)
    if any(not torch.equal(frozen.state_dict()[k].detach().cpu(),value)
           for k,value in original.items()):
        raise ValueError('GAMER_G2_0022_FROZEN_GUARD_MUTATED')
    return models,{'steps':STEPS,'lr':LR,'seed':seed,'counts':dict(totals),
                   'last_train_loss':last,'train_cases':len(cases),
                   'common_batches':True,'common_initialization':True,
                   'label_blind_top24':True,'full_selected_groups':True,
                   'multi_positive_preserved':True}


def _competition_failures(edges,groups,logits,gold):
    by_group=defaultdict(list)
    for i,g in enumerate(groups):by_group[g].append(i)
    total=behind=0
    for ids in by_group.values():
        negatives=[float(logits[i]) for i in ids if edges[i] not in gold]
        if not negatives:continue
        best=max(negatives)
        for i in ids:
            if edges[i] in gold:
                total+=1
                behind+=float(logits[i])<=best
    return {'gold_with_competing_negatives':total,'gold_not_ahead_of_hardest_negative':behind}


def evaluate_case(case,row,head,guard,arms,allowed,*,split,index,device,parent21=None):
    if case['prompt_sha256']!=base._sha(row['prompt']) or case['family']!=row['family']:
        raise ValueError('GAMER_G2_0022_CASE_IDENTITY')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        if len(inv['edges'])!=len(set(inv['edges'])):
            raise ValueError('GAMER_G2_0022_DUPLICATE_EVAL_CANDIDATES')
        x=inv['features'].to(device);b=inv['base'].to(device)
        nets={'frozen_guard_0019':guard,**arms}
        scores={arm:net(x,b,inv['groups'],with_competition=True).detach().cpu()
                for arm,net in nets.items()}
        metrics={arm:sel.score_set(source13.decode_scores(inv,scores[arm],THRESHOLD),case['gold'])
                 for arm in ARMS}
        competition={arm:_competition_failures(inv['edges'],inv['groups'],scores[arm],case['gold'])
                     for arm in ARMS}
        reachable=len(set(inv['edges'])&case['gold'])
        if parent21 is not None:
            prior={tuple(map(tuple,c['edge'][:2]))+(c['edge'][2],):c['score']
                   for c in parent21['candidates']}
            if (parent21['prompt_sha256']!=case['prompt_sha256'] or
                parent21['fixed_score']!=metrics['frozen_guard_0019'] or
                parent21['reachable_gold']!=reachable or
                len(prior)!=len(inv['edges']) or
                any(edge not in prior or float(scores['frozen_guard_0019'][i])!=prior[edge]
                    for i,edge in enumerate(inv['edges']))):
                raise ValueError('GAMER_G2_0022_0021_LIVE_REPLAY_DRIFT')
    return {'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
            'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
            'structural_topology':row.get('structural_topology','native'),
            'gold_total':len(case['gold']),'reachable_gold':reachable,
            'candidate_count':len(inv['edges']), 'configurations':metrics,
            'competition':competition}


def aggregate(records):
    if not records:raise ValueError('GAMER_G2_0022_EMPTY_METRICS')
    return {'cases':len(records),
            'metrics':{arm:base.metrics([{**r['configurations'][arm],'family':r['family']}
                                         for r in records]) for arm in ARMS},
            'competition':{arm:{k:sum(r['competition'][arm][k] for r in records)
                                for k in ('gold_with_competing_negatives',
                                          'gold_not_ahead_of_hardest_negative')}
                           for arm in ARMS},
            'reachable_gold':sum(r['reachable_gold'] for r in records),
            'gold_total':sum(r['gold_total'] for r in records)}


def gates(agg):
    base_m=agg['metrics']['frozen_guard_0019']
    control=agg['metrics']['matched_finetune'];arm=agg['metrics']['competition_aware']
    rank=agg['competition']
    return {'competition_f1_above_frozen':arm['f1']>base_m['f1'],
            'competition_f1_above_matched_control':arm['f1']>control['f1'],
            'competition_precision_above_matched_control':arm['precision']>control['precision'],
            'competition_recall_at_least_matched_minus_002':arm['recall']>=control['recall']-.02,
            'competition_hard_negative_errors_below_matched':
                rank['competition_aware']['gold_not_ahead_of_hardest_negative'] <
                rank['matched_finetune']['gold_not_ahead_of_hardest_negative']}


def _save_cases(groups,path):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
            for split in SPLITS:
                for row in groups[split]:
                    z.write((json.dumps(row,sort_keys=True,separators=(',',':'),
                                        allow_nan=False)+'\n').encode())


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,output,
        device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0022_IMMUTABLE_OUTPUT')
    if steps!=STEPS:raise ValueError('GAMER_G2_0022_FIXED_STEPS')
    # Reconstruct and verify frozen ancestry, full parent identities and the
    # complete G2-0021 case ledger before touching the training weights.
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0022_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0022_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0022_G1_LINEAGE')
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
        raise ValueError('GAMER_G2_0022_PARENT_0016_LINEAGE')
    exposed=dict(zip(ceiling20.EXPOSED,(independent,withheld)))
    rec17,h17=context18.verify_parent_0017(ceiling_run,exposed,h16)
    h18=train19.verify_parent_0018(context18_run,exposed,h16,h17,rec17)
    _,h19=ceiling20.verify_parent_0019(train19_run,{'calibration':calibration,**exposed},rec16,
        {**h16,**h17,**h18},expected_train_sha=density.sha_rows(training),
        expected_datasets={s:density.sha_rows(rows) for s,rows in
                           (('calibration',calibration),*exposed.items())})
    prior=[r for batch in (*history,augmented,calibration,independent,withheld) for r in batch]
    fresh20=ceiling20.fresh_cohorts(prior)
    rec20,h20=ceiling21.verify_parent_0020(ceiling20_run,{**exposed,**fresh20},
                                            {**h16,**h17,**h18,**h19})
    fresh21=ceiling21._cohort_rows(prior,fresh20)
    parent_rows={**fresh20,**fresh21}
    parent21,previous,h21=_read_parent21(ceiling21_run,parent_rows,
                                          {**h16,**h17,**h18,**h19,**h20})
    newer=fresh_cohorts(prior,fresh20,fresh21)
    to_evaluate={**fresh21,**newer}
    if len(training)!=640:raise ValueError('GAMER_G2_0022_TRAIN_COUNT_DRIFT')
    all_sets=[training,calibration,independent,withheld,*fresh20.values(),*fresh21.values(),*newer.values()]
    if len({base._sha(r['prompt']) for rows in all_sets for r in rows}) != sum(map(len,all_sets)):
        raise ValueError('GAMER_G2_0022_TRAIN_DEV_OVERLAP')
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
                                     parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
                                                      manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0022_BACKBONE_UNFROZEN')
    allowed=sel.compatibility_from_train(original)
    guard=None;captured={}
    for split,rows in [('train',training),*to_evaluate.items()]:
        captured[split]=rel.capture(head,backbone_model,tokenizer,rows,device=device,
                                    max_length=max_length,group=split)
        if len(captured[split])!=len(rows):
            raise ValueError('GAMER_G2_0022_CAPTURE_COUNT')
        if guard is None:
            width=captured[split][0]['vectors'].shape[1]
            parent19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
            guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                         parent19['new_weights_sha256']).to(device)
            guard_before={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
        if any(c['vectors'].shape[1]!=width for c in captured[split]):
            raise ValueError('GAMER_G2_0022_WIDTH_DRIFT')
    for row,case in zip(training,captured['train']):
        case.update({'rank':rel.full_logits(head,case['vectors'].to(device)).detach().cpu(),
                     'allowed':allowed,'split':row['split'],
                     'structural_topology':row.get('structural_topology','native')})
    trained,train_info=train(captured['train'],guard,steps=steps,device=device)
    groups={}
    for split,rows in to_evaluate.items():
        out=[]
        for i,(case,row) in enumerate(zip(captured[split],rows),1):
            older=previous[split][i-1] if split in REPLAY else None
            out.append(evaluate_case(case,row,head,guard,trained,allowed,split=split,
                                     index=i,device=device,parent21=older))
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_0022_EVALUATE {split} {i}/{len(rows)}',flush=True)
        groups[split]=out
    rel.validate_original_state(head,head_before)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),value)
           for k,value in guard_before.items()):
        raise ValueError('GAMER_G2_0022_GUARD_MUTATED')
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0022_BACKBONE_TRAINED')
    cohort={'replayed_0021_dev':[r for split in REPLAY for r in groups[split]],
            'new_0022_dev':[r for split in NEW for r in groups[split]]}
    result={'schema':SCHEMA,'status':'G2_COMPETITION_AWARE_LEARNING_DEV_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(rows) for k,rows in to_evaluate.items()},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in newer[k]] for k in NEW},
        'predeclared':{'train_seed':SEED,'new_dev_seeds':[EVAL_SEED,EVAL_SEED+1],
           'steps':STEPS,'lr':LR,'top_k':TOP_K,'groups_per_case':GROUPS_PER_CASE,
           'fixed_threshold':THRESHOLD,'arms':list(ARMS),
           'extra_hard_negative_weight':.75,'hard_negative_margin':1.25,
           'max_hard_negatives_per_group':3},
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
        'candidate_selection_uses_gold':False,'hard_negative_labels_train_only':True,
        'multi_positive_supervision':True,'same_train_batches':True,
        'same_initial_checkpoint':True,'frozen_g1':True,'frozen_rank_head':True,
        'frozen_backbone':True,'frozen_parent_guard':True,
        'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'certification_authorized':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    save_file({arm+'.'+key:value.detach().cpu().contiguous()
               for arm,net in trained.items() for key,value in net.state_dict().items()},
              str(output/WEIGHTS))
    result['new_weights_sha256']=backbone.sha256(output/WEIGHTS)
    _save_cases(groups,output/CASES)
    result['cases_sha256']=backbone.sha256(output/CASES)
    (output/REPORT).write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n',
                               encoding='utf-8')
    return result
