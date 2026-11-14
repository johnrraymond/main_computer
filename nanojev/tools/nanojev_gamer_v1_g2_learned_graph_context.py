"""G2-0008: train a new graph-context edge discriminator; frozen G1 and G2.

Training uses TRAIN answers only as labels, and G1-predicted nodes exclusively
as model inputs. Frozen G2-0005 edges supply one-pass neighborhood evidence;
a candidate edge is removed from its *own* context. G2-0007 DEV is calibration
only; a new prompt-disjoint DEV cohort is evaluated after rule selection.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import random

import torch
from torch import nn
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA='nanojev.gamer.graph.g2.learned-graph-context.v1'
REPORT='g2_learned_graph_context_report.json'
CASES='g2_learned_graph_context_per_case.jsonl'
WEIGHTS='g2_learned_graph_context_development.safetensors'
SEED=380008
VALIDATION_LIMIT=320
STEPS=180
BATCH=512
LR=3e-4
THRESHOLDS=(.40,.45,.50,.55,.60,.65,.70)
PARENT='g2_0005_frozen_selected'
EXTRA=9+len(BIND_CLASSES)


def new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,*,limit=VALIDATION_LIMIT,seed=SEED):
    earlier=train+first+second+third+fourth+fifth+sixth+seventh
    if limit<2 or limit%2 or not all((train,first,second,third,fourth,fifth,sixth,seventh)):
        raise ValueError('GAMER_G2_0008_BAD_COHORT')
    fresh=base.novel_dev_rows(earlier,limit=limit,seed=seed)
    hashes=[base._sha(x['prompt']) for x in fresh]
    if len(set(hashes))!=len(hashes) or set(hashes)&{base._sha(x['prompt']) for x in earlier}:
        raise ValueError('GAMER_G2_0008_PROMPT_LEAKAGE')
    if Counter(x['family'] for x in fresh)!={'encounter':limit//2,'allocation':limit//2}:
        raise ValueError('GAMER_G2_0008_HOLDOUT_IMBALANCE')
    return fresh


def verify_context7_parent(directory,seventh,expected_hashes,motifs_hash):
    """Verify the previous failed experiment and recompute *all* saved metrics."""
    directory=Path(directory)
    path=directory/context7.REPORT;cases_path=directory/context7.CASES
    report=json.loads(path.read_text(encoding='utf-8'))
    required={'schema':context7.SCHEMA,'g1_frozen':True,'g2_0004_rank_weights_frozen':True,
              'g2_0005_rule_frozen':True,'trainable_parameters':0,'selected_rule':None,
              'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
              'certification_authorized':False,'graph_execution_authorized':False,
              'challenge_or_test_split_used':False,'sealed_test_used':False,
              'inference_uses_predicted_g1_only':True,'parent_g2_0006_replay_pass':True,
              'parent_g2_0006_screen_pass':False,'parent_g2_0005_screen_pass':True,
              'independent_cases':len(seventh),'independent_seed':context7.SEED,
              'independent_dataset_sha256':context7.sha_rows(seventh),
              'g1_development_candidate':base.G1_ARM,'g1_head_sha256':base.G1_HEAD_SHA,
              'train_motif_audit_sha256':motifs_hash}
    for name,value in required.items():
        if type(report.get(name)) is not type(value) or report[name]!=value:
            raise ValueError('GAMER_G2_0008_G2_0007_CONTRACT:'+name)
    if report.get('parent_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0008_G2_0007_LINEAGE')
    rules={name:{'positive_bonus':p,'negative_penalty':n} for name,(p,n) in context7.VARIANTS.items()}
    if report.get('fixed_decoder_variants')!=rules:
        raise ValueError('GAMER_G2_0008_G2_0007_RULES')
    saved=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    cal=report.get('calibration_cases')
    if type(cal) is not int or cal<1 or len(saved)!=cal+len(seventh):
        raise ValueError('GAMER_G2_0008_G2_0007_CASE_COUNT')
    names={PARENT,*context7.VARIANTS}
    for index,item in enumerate(saved):
        group='previously_inspected_calibration' if index<cal else 'new_independent_validation'
        local=index+1 if index<cal else index-cal+1
        if item.get('group')!=group or item.get('index')!=local or set(item.get('configurations',{}))!=names:
            raise ValueError('GAMER_G2_0008_G2_0007_CASE_SCHEMA')
        if index>=cal:
            original=seventh[index-cal]
            if (item['prompt_sha256']!=base._sha(original['prompt']) or
                item['family']!=original['family'] or
                item['gold_binding_edges']!=len(base.gold_binding_edges(original))):
                raise ValueError('GAMER_G2_0008_G2_0007_CASE_IDENTITY')
    for field,subset in (('calibration_metrics',saved[:cal]),('independent_metrics',saved[cal:])):
        replay={name:base.metrics([{**item['configurations'][name],'family':item['family']}
                                   for item in subset]) for name in names}
        if replay!=report.get(field):raise ValueError('GAMER_G2_0008_G2_0007_METRICS:'+field)
    selected,selection=context7.choose_rule(report['calibration_metrics'])
    if selected is not None or selection!=report.get('calibration_selection'):
        raise ValueError('GAMER_G2_0008_G2_0007_SELECTION')
    return report,saved[cal:],{'g2_0007_report_sha256':backbone.sha256(path),
                              'g2_0007_cases_sha256':backbone.sha256(cases_path)}


class GraphContextScorer(nn.Module):
    """Only trainable module: context-aware residual logit of a candidate edge."""
    def __init__(self,width,hidden=64):
        super().__init__()
        if type(width) is not int or width<4:raise ValueError('GAMER_G2_0008_BAD_WIDTH')
        self.width=width
        self.network=nn.Sequential(nn.Linear(6*width+EXTRA,hidden),nn.GELU(),
                                   nn.Linear(hidden,hidden//2),nn.GELU(),
                                   nn.Linear(hidden//2,1))
        # Begin at the frozen adjusted odds, rather than a random graph policy.
        nn.init.zeros_(self.network[-1].weight)
        nn.init.zeros_(self.network[-1].bias)

    def forward(self,features,base_odds):
        if features.ndim!=2 or features.shape[1]!=6*self.width+EXTRA or base_odds.shape!=(len(features),):
            raise ValueError('GAMER_G2_0008_BAD_MODEL_INPUT')
        return base_odds+self.network(features).squeeze(-1)


def graph_features(anchors,vectors,features,parent,rule,*,with_context=True):
    """Frozen predicted-node neighborhood features; no gold labels or teacher edges.

    The candidate itself is leave-one-out in the parent graph, preventing it
    from creating its own contextual evidence. All neighbor vectors are frozen.
    """
    if vectors.ndim!=2 or len(anchors)!=vectors.shape[0] or len(set(anchors))!=len(anchors):
        raise ValueError('GAMER_G2_0008_BAD_NODES')
    n,width=vectors.shape
    if width<4 or n<1 or n>128 or not torch.isfinite(vectors).all():
        raise ValueError('GAMER_G2_0008_BAD_VECTORS')
    density.select_edges([],rule)
    anchors_idx={node:i for i,node in enumerate(anchors)}
    frozen=vectors.detach().float().cpu()
    sums=torch.zeros((n,2,width),dtype=torch.float32);degrees=torch.zeros((n,2),dtype=torch.float32)
    for src,dst,relation in parent:
        if src not in anchors_idx or dst not in anchors_idx or relation not in BIND_CLASSES[1:]:
            raise ValueError('GAMER_G2_0008_BAD_PARENT_EDGE')
        a=anchors_idx[src];b=anchors_idx[dst]
        sums[a,0]+=frozen[b];sums[b,1]+=frozen[a]
        degrees[a,0]+=1;degrees[b,1]+=1
    output=[];base_odds=[];edge_labels=[]
    for feature in features:
        if len(feature)!=7:raise ValueError('GAMER_G2_0008_BAD_CANDIDATE')
        src,dst,relation,confidence,odds,count,gap=feature
        if (src not in anchors_idx or dst not in anchors_idx or relation not in BIND_CLASSES[1:] or
            type(count) is not int or count<1 or
            not all(math.isfinite(x) for x in (confidence,odds,gap))):
            raise ValueError('GAMER_G2_0008_BAD_CANDIDATE')
        i=anchors_idx[src];j=anchors_idx[dst]
        own=(src,dst,relation) in parent
        src_out_deg=degrees[i,0]-(1. if own else 0.)
        dst_in_deg=degrees[j,1]-(1. if own else 0.)
        src_out_sum=sums[i,0]-(frozen[j] if own else 0.)
        dst_in_sum=sums[j,1]-(frozen[i] if own else 0.)
        context=[src_out_sum/src_out_deg.clamp(min=1),sums[i,1]/degrees[i,1].clamp(min=1),
                 sums[j,0]/degrees[j,0].clamp(min=1),dst_in_sum/dst_in_deg.clamp(min=1)]
        degrees_context=[src_out_deg,degrees[i,1],degrees[j,0],dst_in_deg]
        if not with_context:
            context=[torch.zeros_like(frozen[i]) for _ in range(4)]
            degrees_context=[torch.tensor(0.) for _ in range(4)]
        relation_hot=torch.zeros(len(BIND_CLASSES));relation_hot[BIND_CLASSES.index(relation)]=1.
        extras=torch.stack((torch.tensor(float(odds)),torch.tensor(float(confidence)),
                            torch.tensor(math.log1p(count-1)),torch.tensor(min(20.,float(gap))),
                            *[torch.log1p(value) for value in degrees_context],
                            torch.tensor(float(own))))
        raw=torch.cat((frozen[i],frozen[j],*context,extras,relation_hot))
        output.append(raw)
        base_odds.append(odds-rule['density_penalty']*math.log1p(count-1)-rule['competition_penalty']*gap)
        edge_labels.append((src,dst,relation))
    matrix=torch.stack(output) if output else torch.empty((0,6*width+EXTRA))
    scores=torch.tensor(base_odds,dtype=torch.float32)
    if matrix.shape!=(len(features),6*width+EXTRA) or not torch.isfinite(matrix).all() or not torch.isfinite(scores).all():
        raise ValueError('GAMER_G2_0008_NONFINITE_FEATURES')
    return matrix,scores,edge_labels


def encode_cases(cases,head,allowed,rule,*,device,group):
    cache=[]
    with torch.inference_mode():
        for index,item in enumerate(cases,1):
            rank=rel.full_logits(head,item['vectors'].to(device)).detach().cpu()
            features=density.candidates(item['anchors'],rank,allowed)
            parent=density.select_edges(features,rule)
            matrix,scores,edges=graph_features(item['anchors'],item['vectors'],features,parent,rule)
            no_context,_,_=graph_features(item['anchors'],item['vectors'],features,parent,rule,with_context=False)
            cache.append({**item,'features':matrix,'without_context':no_context,
                          'base_scores':scores,'edges':edges,'parent':parent})
            if index==1 or index%40==0 or index==len(cases):
                print(f'GAMER_G2_LEARNED_GRAPH_ENCODE {group} {index}/{len(cases)}',flush=True)
    return cache


def train_context(net,cases,*,steps=STEPS,batch=BATCH,lr=LR,seed=SEED,device='cpu',feature_name='features'):
    if type(steps) is not int or steps<1 or type(batch) is not int or batch<2:
        raise ValueError('GAMER_G2_0008_BAD_TRAIN_STEPS')
    if not cases or any('gold' not in item for item in cases) or feature_name not in ('features','without_context'):
        raise ValueError('GAMER_G2_0008_NO_TRAIN_CASES')
    # Hard negatives prioritized by frozen ranking confidence; sample mixed
    # hard and general negatives. The only use of TRAIN gold is target labels.
    matrices=[];scores=[];labels=[];count=Counter()
    rng=random.Random(seed)
    for item in cases:
        positive=[i for i,e in enumerate(item['edges']) if e in item['gold']]
        negative=[i for i,e in enumerate(item['edges']) if e not in item['gold']]
        negative.sort(key=lambda i:(-float(item['base_scores'][i]),i))
        kept=negative[:max(8,6*len(positive))]
        tail=negative[len(kept):]
        kept+=rng.sample(tail,min(len(tail),max(4,len(positive))))
        indices=positive+kept
        if not indices:continue
        matrices.append(item[feature_name][indices]);scores.append(item['base_scores'][indices])
        labels.extend([1.]*len(positive)+[0.]*len(kept))
        count['positive']+=len(positive);count['negative']+=len(kept)
    if not count['positive'] or not count['negative']:
        raise ValueError('GAMER_G2_0008_NO_BINARY_TRAIN_SIGNAL')
    X=torch.cat(matrices);O=torch.cat(scores);Y=torch.tensor(labels,dtype=torch.float32)
    if not torch.isfinite(X).all():raise ValueError('GAMER_G2_0008_NONFINITE_TRAIN')
    net=net.to(device).train();optimizer=torch.optim.AdamW(net.parameters(),lr=lr,weight_decay=0.001)
    gen=torch.Generator().manual_seed(seed)
    n=len(Y);last=None
    for step in range(steps):
        ids=torch.randint(n,(min(batch,n),),generator=gen)
        inp=X[ids].to(device);odds=O[ids].to(device);target=Y[ids].to(device)
        output=net(inp,odds)
        # Balanced class contribution: prevent negatives from dominating.
        weights=torch.where(target>0.5,float(count['negative'])/count['positive'],1.)
        loss=F.binary_cross_entropy_with_logits(output,target,weight=weights)
        if not torch.isfinite(loss):raise ValueError('GAMER_G2_0008_NONFINITE_LOSS')
        optimizer.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        optimizer.step();last=float(loss.detach())
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_LEARNED_GRAPH_TRAIN {step+1}/{steps} loss={last:.6f}',flush=True)
    net.eval().requires_grad_(False)
    return {'steps':steps,'last_loss':last,'samples':dict(count),'train_cases':len(cases),
            'feature_name':feature_name}


def evaluate(cases,net,control,*,device,group,thresholds=THRESHOLDS):
    summaries=defaultdict(list);records=[]
    names=[f'learned_p{round(t*100):02d}' for t in thresholds]
    names += [f'no_neighborhood_p{round(t*100):02d}' for t in thresholds] + [PARENT]
    with torch.inference_mode():
        for item in cases:
            raw=net(item['features'].to(device),item['base_scores'].to(device)).detach().cpu()
            ablated=control(item['without_context'].to(device),item['base_scores'].to(device)).detach().cpu()
            if not torch.isfinite(raw).all() or not torch.isfinite(ablated).all():
                raise ValueError('GAMER_G2_0008_NONFINITE_EVALUATION')
            results={PARENT:sel.score_set(item['parent'],item['gold'])}
            for threshold in thresholds:
                cut=math.log(threshold/(1.-threshold))
                found={edge for edge,score in zip(item['edges'],raw) if float(score)>=cut}
                results[f'learned_p{round(threshold*100):02d}']=sel.score_set(found,item['gold'])
            for threshold in thresholds:
                cut=math.log(threshold/(1.-threshold))
                same={edge for edge,score in zip(item['edges'],ablated) if float(score)>=cut}
                results[f'no_neighborhood_p{round(threshold*100):02d}']=sel.score_set(same,item['gold'])
            for name,value in results.items():summaries[name].append({**value,'family':item['family']})
            records.append({'group':group,'index':item['index'],'family':item['family'],
                            'prompt_sha256':item['prompt_sha256'],'gold_binding_edges':len(item['gold']),
                            'g1_predicted_nodes':len(item['anchors']),
                            'density_bucket':density._bucket(len(item['anchors'])),
                            'candidate_edges':len(item['edges']),'configurations':results})
    return {name:base.metrics(rows) for name,rows in summaries.items()},records


def choose_rule(calibration):
    parent=calibration[PARENT];floor=max(.60,parent['recall']-.02)
    choices=[name for name in calibration if name.startswith('learned_p') and
             calibration[name]['recall']>=floor and
             calibration[name]['false_positive']<=parent['false_positive'] and
             calibration[name]['f1']>parent['f1']]
    if not choices:return None,{'eligible_count':0,'recall_floor':floor,'reason':'NO_LEARNED_GRAPH_RULE_BEATS_PARENT'}
    best=max(choices,key=lambda name:(calibration[name]['f1'],calibration[name]['recall'],
                                      -calibration[name]['false_positive'],name))
    return best,{'eligible_count':len(choices),'recall_floor':floor,
                 'reason':'MAX_CALIBRATION_F1_WITH_PARENT_NONREGRESSION'}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,relational_run,
        density_run,consistency_run,context_run,output,device='cuda',max_length=2048,
        validation_limit=VALIDATION_LIMIT,steps=STEPS):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0008_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,rel.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(item['prompt']) for item in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0008_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=rel.CALIBRATION_LIMIT,seed=base.SEED)
    if density.sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0008_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=rel.new_validation_rows(train,first,second,third,limit=rel.VALIDATION_LIMIT)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=density.VALIDATION_LIMIT)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=graph6.VALIDATION_LIMIT)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=context7.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    motifs,motif_audit=context7.learn_motifs(train,allowed)
    functional,train_evidence=graph6.learn_constraints(train,allowed)
    selection_dir=Path(selection_run)
    selection=json.loads((selection_dir/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
        selection.get('validation_dataset_sha256')!=density.sha_rows(second) or
        selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
        selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0008_G2_0002_DRIFT')
    early={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_dir/sel.REPORT),
           'g2_0002_cases_sha256':backbone.sha256(selection_dir/sel.CASES)}
    _,disc_hashes=rel.verify_discrimination_parent(discrimination_run,third,early)
    bootstrap_head=deepcopy(head)
    _,_,fourth_hashes=density.verify_parent(relational_run,fourth,head,
                                            {**hashes,**disc_hashes},allowed)
    fifth_report,_,fifth_hashes=graph6.verify_density_parent(density_run,fifth,
                                           {**hashes,**disc_hashes,**fourth_hashes})
    _,_,sixth_hashes=context7.verify_graph6_parent(consistency_run,sixth,
                       {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes},
                       sel._hash_json(train_evidence))
    seventh_report,seventh_saved,seventh_hashes=verify_context7_parent(context_run,seventh,
                         {**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes},
                         sel._hash_json(motif_audit))
    fresh=new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=validation_limit)
    rule=fifth_report['fixed_decoder_rules'][graph6.PARENT_ARM]
    original={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=bootstrap_head.to(device).eval().requires_grad_(False)
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0008_BACKBONE_UNFROZEN')
    train_capture=rel.capture(bootstrap_head,model,tok,train,device=device,max_length=max_length,group='train')
    cal_capture=rel.capture(bootstrap_head,model,tok,seventh,device=device,max_length=max_length,group='g2_0007_replay')
    fresh_capture=rel.capture(bootstrap_head,model,tok,fresh,device=device,max_length=max_length,group='new_independent')
    # Verify the frozen G2-0007 *predictions*, not just its saved aggregate.
    with torch.inference_mode():
        prior_metrics,prior_cases=context7.evaluate(cal_capture,head,allowed,motifs,device=device,
                            group='new_independent_validation',parent_rule=rule)
        if prior_metrics!=seventh_report['independent_metrics'] or len(prior_cases)!=len(seventh_saved):
            raise ValueError('GAMER_G2_0008_G2_0007_REPLAY_METRICS')
        for now,old in zip(prior_cases,seventh_saved):
            if (now['configurations']!=old['configurations'] or
                now['prompt_sha256']!=old['prompt_sha256'] or
                now['g1_predicted_nodes']!=old['g1_predicted_nodes']):
                raise ValueError('GAMER_G2_0008_G2_0007_REPLAY_PREDICTIONS')
        encoded_train=encode_cases(train_capture,head,allowed,rule,device=device,group='train')
        encoded_cal=encode_cases(cal_capture,head,allowed,rule,device=device,group='calibration')
        encoded_fresh=encode_cases(fresh_capture,head,allowed,rule,device=device,group='new_independent')
    width=encoded_train[0]['vectors'].shape[1]
    torch.manual_seed(SEED)
    learned=GraphContextScorer(width)
    control=deepcopy(learned)  # exactly matched initial weights and optimizer schedule
    training=train_context(learned,encoded_train,steps=steps,device=device)
    control_training=train_context(control,encoded_train,steps=steps,device=device,
                                   feature_name='without_context')
    calibration,cal_saved=evaluate(encoded_cal,learned,control,device=device,
                                    group='previously_inspected_calibration')
    selected,selection=choose_rule(calibration)
    ablation_cal={PARENT:calibration[PARENT],**{key.replace('no_neighborhood_','learned_'):value
                   for key,value in calibration.items() if key.startswith('no_neighborhood_')}}
    ablation_selected,ablation_selection=choose_rule(ablation_cal)
    if ablation_selected is not None:
        ablation_selected=ablation_selected.replace('learned_','no_neighborhood_')
    independent,fresh_saved=evaluate(encoded_fresh,learned,control,device=device,
                                       group='new_independent_validation')
    rel.validate_original_state(head,original)
    parent=independent[PARENT]
    crowd=density.bucket_metrics(fresh_saved,(PARENT,*[x for x in independent if x.startswith('learned_p')],
                                                  *[x for x in independent if x.startswith('no_neighborhood_')]))
    crowded=crowd['crowded_49_128']
    has_crowded=crowded.get(PARENT,{}).get('cases',0)>0
    gates={name:{'f1_improved':independent[name]['f1']>parent['f1'],
                 'no_false_positive_regression':independent[name]['false_positive']<=parent['false_positive'],
                 'recall_at_least_060':independent[name]['recall']>=.60,
                 'recall_at_least_parent_minus_002':independent[name]['recall']>=parent['recall']-.02,
                 'crowded_f1_improved':bool(has_crowded and crowded[name]['f1']>crowded[PARENT]['f1']),
                 'crowded_recall_at_least_parent_minus_005':bool(has_crowded and
                     crowded[name]['recall']>=crowded[PARENT]['recall']-.05),
                 'no_gold_used_as_inference_input':True}
           for name in independent if name.startswith('learned_p')}
    candidate=gates.get(selected)
    ablation=independent.get(ablation_selected)
    report={'schema':SCHEMA,'status':'G2_LEARNED_GRAPH_CONTEXT_DEV_NOT_CERTIFIED',
            'g1_frozen':True,'g1_development_candidate':base.G1_ARM,
            'g2_0004_rank_weights_frozen':True,'g2_0005_rule_frozen':True,
            'g2_0007_replay_pass':True,'g2_0007_screen_pass':False,
            'g2_context_trainable_parameters':sum(p.numel() for p in learned.parameters()),
            'control_trainable_parameters':sum(p.numel() for p in control.parameters()),
            'g2_certified':False,'g2_production_unlocked':False,
            'production_promotion':False,'certification_authorized':False,
            'graph_execution_authorized':False,'memory_writes':False,'sealed_test_used':False,
            'challenge_or_test_split_used':False,'inference_uses_predicted_g1_only':True,
            'no_gold_graph_at_inference':True,'gold_used_for_train_supervision_and_offline_scoring_only':True,
            'g1_head_sha256':manifest['head_sha256'],
            'parent_hashes':{**hashes,**disc_hashes,**fourth_hashes,**fifth_hashes,**sixth_hashes,**seventh_hashes},
            'train_cases':len(train),'train_info':training,'control_train_info':control_training,
            'training_seed':SEED,
            'training_scoring_only_context_parameters':True,
            'calibration_cases':len(seventh),'calibration_seed':context7.SEED,
            'calibration_dataset_sha256':context7.sha_rows(seventh),
            'independent_cases':len(fresh),'independent_seed':SEED,
            'independent_dataset_sha256':context7.sha_rows(fresh),
            'thresholds_predeclared':list(THRESHOLDS),
            'calibration_selection':selection,'selected_rule':selected,
            'ablation_calibration_selection':ablation_selection,
            'ablation_selected_rule':ablation_selected,
            'calibration_metrics':calibration,'independent_metrics':independent,
            'selected_independent_screen_pass':bool(
                candidate and all(candidate.values()) and ablation and selected and
                independent[selected]['f1']>ablation['f1']),
            'selected_beats_trained_no_neighborhood_f1':bool(
                selected and ablation and independent[selected]['f1']>ablation['f1']),
            'independent_gates_by_rule':gates,
            'independent_by_node_density':crowd,
            'ablation_no_neighborhood_p50':independent['no_neighborhood_p50'],
            'no_fixed_cardinality':True,'no_iterative_graph_completion':True}
    output.mkdir(parents=True)
    safe={'context.'+key:value.detach().cpu().contiguous() for key,value in learned.state_dict().items()}
    safe.update({'no_neighborhood.'+key:value.detach().cpu().contiguous()
                 for key,value in control.state_dict().items()})
    save_file(safe,str(output/WEIGHTS))
    report['context_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(cal_saved+fresh_saved))
    return report
