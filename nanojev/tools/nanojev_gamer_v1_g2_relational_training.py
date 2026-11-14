"""G2-0004: train-only hard negatives and relation-aware ranking on predicted G1 nodes.

Development experiment, not a G1 certificate, G2 production unlock, or authority
for graph execution. Both comparison heads start at the immutable G2-0001
weights. G1, backbone, G2-0001 weights and G2-0003 fitted rules stay frozen.
Gold edges are available exclusively to training loss and offline evaluation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import random

import torch
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as select
from tools import nanojev_gamer_v1_g2_binding_discrimination as discrim
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA='nanojev.gamer.graph.g2.hard-negative-relational-training.v1'
REPORT='g2_relational_training_report.json'
CASES='g2_relational_training_per_case.jsonl'
WEIGHTS='g2_relational_rank_development.safetensors'
SEED=340004
TRAIN_LIMIT=base.TRAIN_LIMIT
CALIBRATION_LIMIT=base.DEV_LIMIT
VALIDATION_LIMIT=320
STEPS=200
BATCH=4
LR=1e-4
MAX_HARD_NEGATIVES=384
HARD_NEG_PER_POSITIVE=6
RANK_NEG_PER_POSITIVE=4
RANK_MARGIN=0.75
RANK_WEIGHT=0.5
THRESHOLDS=(.50,.60,.70,.80,.90)


def _sha_rows(rows):
    return hashlib.sha256(base._jsonl(rows)).hexdigest()


def new_validation_rows(train,old_dev,second,third,*,limit=VALIDATION_LIMIT,seed=SEED):
    if limit<2 or limit%2 or not all((train,old_dev,second,third)):
        raise ValueError('GAMER_G2_0004_BAD_HOLDOUT')
    prior=train+old_dev+second+third
    fresh=base.novel_dev_rows(prior,limit=limit,seed=seed)
    hashes=[base._sha(row['prompt']) for row in fresh]
    if len(set(hashes))!=len(hashes) or set(hashes)&{base._sha(row['prompt']) for row in prior}:
        raise ValueError('GAMER_G2_0004_PROMPT_LEAKAGE')
    if Counter(row['family'] for row in fresh)!={'encounter':limit//2,'allocation':limit//2}:
        raise ValueError('GAMER_G2_0004_HOLDOUT_IMBALANCE')
    return fresh


def verify_discrimination_parent(run_dir,third,expected_bootstrap_hashes):
    """Verify exact prior cohort and immutable decision, never refit its rules."""
    run_dir=Path(run_dir)
    report_path=run_dir/discrim.REPORT
    cases_path=run_dir/discrim.CASES
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={'schema':discrim.SCHEMA,'g1_frozen':True,
              'g2_development_weights_frozen':True,'trainable_parameters':0,
              'g2_certified':False,'g2_production_unlocked':False,
              'production_promotion':False,'certification_authorized':False,
              'graph_execution_authorized':False,'memory_writes':False,
              'sealed_test_used':False,'challenge_or_test_split_used':False,
              'oracle_input_to_decoder':False,'train_only_compatibility':True,
              'g2_0002_replay_pass':True,
              'selection_split':'g2_0002_previously_inspected_dev_independent',
              'validation_split':'new_prompt_disjoint_dev',
              'g1_development_candidate':base.G1_ARM,
              'independent_seed':discrim.SEED,
              'independent_validation_cases':len(third),
              'independent_validation_dataset_sha256':_sha_rows(third)}
    for name,expected in required.items():
        if type(report.get(name)) is not type(expected) or report[name]!=expected:
            raise ValueError('GAMER_G2_0004_PARENT_CONTRACT:'+name)
    for key,value in expected_bootstrap_hashes.items():
        if report.get('parent_hashes',{}).get(key)!=value:
            raise ValueError('GAMER_G2_0004_PARENT_LINEAGE:'+key)
    fitted=report.get('decoder_rules_calibration_fitted')
    if not isinstance(fitted,dict) or set(fitted)!={
        'relation_confidence_f1','relation_confidence_f2','relation_confidence_f3',
        'source_competition_f1','source_competition_f2','source_competition_f3',
        'dual_relation_source_f2'}:
        raise ValueError('GAMER_G2_0004_PARENT_RULES')
    for name,rule in fitted.items():
        if not isinstance(rule,dict):raise ValueError('GAMER_G2_0004_PARENT_RULE:'+name)
        mode=rule.get('mode')
        if mode not in ('confidence','source_share','dual'):
            raise ValueError('GAMER_G2_0004_PARENT_RULE_MODE:'+name)
        fields=('confidence','source_share') if mode=='dual' else ('thresholds',)
        for field in fields:
            values=rule.get(field)
            if not isinstance(values,dict) or set(values)!=set(discrim.RELATIONS):
                raise ValueError('GAMER_G2_0004_PARENT_RULE_SHAPE:'+name)
            if not all(type(x) in (int,float) and math.isfinite(x) and 0<=x<=1.000001
                       for x in values.values()):
                raise ValueError('GAMER_G2_0004_PARENT_RULE_VALUE:'+name)
    saved=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(saved)!=len(third):raise ValueError('GAMER_G2_0004_PARENT_CASE_COUNT')
    for index,(row,old) in enumerate(zip(third,saved),1):
        if (old.get('group')!='new_independent_validation' or old.get('index')!=index or
                old.get('family')!=row['family'] or old.get('prompt_sha256')!=base._sha(row['prompt']) or
                old.get('gold_binding_edges',-1)!=len(base.gold_binding_edges(row)) or
                set(old.get('configurations',{}))!=set(report.get('independent_validation_metrics',{}))):
            raise ValueError('GAMER_G2_0004_PARENT_CASE_IDENTITY')
    if report.get('selected_rule') is not None and report['selected_rule'] not in fitted:
        raise ValueError('GAMER_G2_0004_PARENT_SELECTED_RULE')
    # Recompute offline metrics from the saved cases without touching inference.
    values={name:[] for name in report['independent_validation_metrics']}
    for old in saved:
        for name,score in old['configurations'].items():
            values[name].append({**score,'family':old['family']})
    if {name:base.metrics(items) for name,items in values.items()}!=report['independent_validation_metrics']:
        raise ValueError('GAMER_G2_0004_PARENT_SAVED_METRICS')
    return report,{'g2_0003_report_sha256':backbone.sha256(report_path),
                   'g2_0003_cases_sha256':backbone.sha256(cases_path)}


def capture(head,model,tok,rows,*,device,max_length,group):
    """Cache predicted nodes and frozen upstream vectors once, never gold nodes."""
    output=[]
    with torch.inference_mode():
        for index,row in enumerate(rows,1):
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            g1=head(hidden,eligible=prepared['eligible'].to(device),active_stage='G1')
            spans=base.predicted_spans(g1,prepared)
            g2=head(hidden,eligible=prepared['eligible'].to(device),training_spans=spans,active_stage='G2')
            anchors=base.predicted_anchors(spans,prepared)
            n=len(anchors)
            if n!=len(spans) or n>128 or n<1:
                raise ValueError('GAMER_G2_0004_BAD_G1_SURFACES')
            logits=g2['binding_logits'][:n,:n].detach().cpu().contiguous()
            vectors=g2['node_vectors'][:n].detach().cpu().contiguous()
            output.append({'index':index,'family':row['family'],'prompt_sha256':base._sha(row['prompt']),
                           'anchors':anchors,'gold':base.gold_binding_edges(row),
                           'logits':logits,'vectors':vectors})
            if index==1 or index%40==0 or index==len(rows):
                print(f'GAMER_G2_RELATIONAL_CAPTURE {group} {index}/{len(rows)}',flush=True)
    return output


def target_pairs(case):
    """Gold targets TRAIN only; unmatched G1 predictions are negative, not dropped."""
    anchors=case['anchors']
    if len(anchors)!=len(set(anchors)):
        raise ValueError('GAMER_G2_0004_DUPLICATE_PREDICTED_NODE')
    lookup={a:i for i,a in enumerate(anchors)}
    pairs={}
    for left,right,relation in case['gold']:
        if relation not in BIND_CLASSES[1:]:
            raise ValueError('GAMER_G2_0004_UNKNOWN_RELATION')
        if left in lookup and right in lookup:
            key=(lookup[left],lookup[right])
            label=BIND_CLASSES.index(relation)
            if key in pairs and pairs[key]!=label:
                raise ValueError('GAMER_G2_0004_MULTI_LABEL_PAIR')
            pairs[key]=label
    positive=[];negative=[]
    for i in range(len(anchors)):
        for j in range(len(anchors)):
            if i==j:continue
            label=pairs.get((i,j),0)
            (positive if label else negative).append((i,j,label))
    return positive,negative


def logits_for_pairs(head,vectors,pairs):
    if not pairs:raise ValueError('GAMER_G2_0004_EMPTY_TRAIN_PAIRS')
    ii=torch.tensor([i for i,j,k in pairs],device=vectors.device,dtype=torch.long)
    jj=torch.tensor([j for i,j,k in pairs],device=vectors.device,dtype=torch.long)
    left=vectors.index_select(0,ii)
    right=vectors.index_select(0,jj)
    return head.binding(F.gelu(head.pair_projection(torch.cat((left,right,left*right,left-right),-1))))


def full_logits(head,vectors):
    n=len(vectors)
    if not n or n>128:raise ValueError('GAMER_G2_0004_BAD_NODE_COUNT')
    left=vectors[:,None,:].expand(n,n,-1)
    right=vectors[None,:,:].expand(n,n,-1)
    return head.binding(F.gelu(head.pair_projection(torch.cat((left,right,left*right,left-right),-1))))


def mine_hard_negatives(head,vectors,positives,negatives,*,seed):
    """Deterministic hard negative mining on TRAIN; no validation labels."""
    if not positives or not negatives:
        raise ValueError('GAMER_G2_0004_INSUFFICIENT_TRAIN_PAIRS')
    with torch.no_grad():
        scores=logits_for_pairs(head,vectors,negatives).detach()
        margins=scores[:,1:].max(-1).values-scores[:,0]
        order=sorted(range(len(negatives)),key=lambda i:(-float(margins[i]),negatives[i][0],negatives[i][1]))
        bound=min(len(negatives),MAX_HARD_NEGATIVES,HARD_NEG_PER_POSITIVE*len(positives))
        chosen=order[:bound]
        # Extra uniformly sampled negatives probe outside the high-score tail.
        remainder=order[bound:]
        rng=random.Random(seed)
        chosen+=rng.sample(remainder,min(len(remainder),max(8,len(positives))))
    samples=[negatives[i] for i in chosen]
    return samples


def ranking_pairs(positives,negatives,head,vectors):
    """For each gold relation, select strongest false destinations from same source."""
    if not positives or not negatives:return []
    by_source={}
    for pos,(_,_,relation) in enumerate(positives):
        i=positives[pos][0]
        by_source.setdefault(i,[]).append((pos,relation))
    with torch.no_grad():
        logits=logits_for_pairs(head,vectors,negatives).detach()
        margins=logits[:,1:]-logits[:,0:1]
    negatives_by_src={}
    for idx,(i,j,_) in enumerate(negatives):
        negatives_by_src.setdefault(i,[]).append(idx)
    ranking=[]
    for i,rows in by_source.items():
        for p,relation in rows:
            eligible=negatives_by_src.get(i,[])
            ordered=sorted(eligible,key=lambda ix:(-float(margins[ix,relation-1]),negatives[ix][1]))
            ranking.extend((p,ix,relation) for ix in ordered[:RANK_NEG_PER_POSITIVE])
    return ranking


def training_loss(head,case,*,mode,seed,device):
    if mode not in ('hardneg_ce','relation_rank'):
        raise ValueError('GAMER_G2_0004_BAD_TRAIN_MODE')
    vectors=case['vectors'].to(device)
    positive,negative=target_pairs(case)
    if not positive or not negative:raise ValueError('GAMER_G2_0004_UNTRAINABLE_CASE')
    hard=mine_hard_negatives(head,vectors,positive,negative,seed=seed)
    pairs=positive+hard
    logits=logits_for_pairs(head,vectors,pairs)
    labels=torch.tensor([k for i,j,k in pairs],device=device,dtype=torch.long)
    pos_loss=F.cross_entropy(logits[:len(positive)],labels[:len(positive)])
    neg_loss=F.cross_entropy(logits[len(positive):],labels[len(positive):])
    loss=(pos_loss+neg_loss)*.5
    ranking=ranking_pairs(positive,hard,head,vectors) if mode=='relation_rank' else []
    if ranking:
        pos_ids=torch.tensor([p for p,n,k in ranking],dtype=torch.long,device=device)
        neg_ids=torch.tensor([len(positive)+n for p,n,k in ranking],dtype=torch.long,device=device)
        kind=torch.tensor([k for p,n,k in ranking],dtype=torch.long,device=device)
        good=logits[pos_ids,kind]-logits[pos_ids,0]
        bad=logits[neg_ids,kind]-logits[neg_ids,0]
        loss=loss+RANK_WEIGHT*F.relu(RANK_MARGIN-good+bad).mean()
    if not torch.isfinite(loss):raise ValueError('GAMER_G2_0004_NONFINITE_LOSS')
    return loss,{'positives':len(positive),'hard_negatives':len(hard),
                 'rank_pairs':len(ranking),'pos_ce':float(pos_loss.detach()),
                 'neg_ce':float(neg_loss.detach())}


def train(head,cache,*,mode,steps=STEPS,batch=BATCH,lr=LR,seed=SEED,device='cpu'):
    if steps<1 or batch<1 or not math.isfinite(lr) or lr<=0:
        raise ValueError('GAMER_G2_0004_BAD_OPTIMIZER')
    count=cur.trainable_stage(head,'G2')
    active={name for name,p in head.named_parameters() if p.requires_grad}
    if not active or not all(name.startswith(('pair_projection.','binding.')) for name in active):
        raise ValueError('GAMER_G2_0004_UPSTREAM_UNFROZEN')
    groups={kind:[case for case in cache if case['family']==kind and target_pairs(case)[0]]
            for kind in ('encounter','allocation')}
    if not all(groups.values()):raise ValueError('GAMER_G2_0004_EMPTY_TRAIN_FAMILY')
    rng=random.Random(seed)
    optimizer=torch.optim.AdamW((p for p in head.parameters() if p.requires_grad),lr=lr)
    history=[];totals=Counter()
    for step in range(steps):
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for k in range(batch):
            family=('encounter','allocation')[(step*batch+k)%2]
            case=rng.choice(groups[family])
            loss,stats=training_loss(head,case,mode=mode,seed=seed+step*batch+k,device=device)
            (loss/batch).backward()
            losses.append(float(loss.detach()))
            totals.update({name:stats[name] for name in ('positives','hard_negatives','rank_pairs')})
        torch.nn.utils.clip_grad_norm_((p for p in head.parameters() if p.requires_grad),1.0)
        optimizer.step()
        history.append(sum(losses)/len(losses))
        if step==0 or (step+1)%40==0 or step+1==steps:
            print(f'GAMER_G2_RELATIONAL_TRAIN {mode} {step+1}/{steps} loss={history[-1]:.6f}',flush=True)
    if not all(math.isfinite(x) for x in history):raise ValueError('GAMER_G2_0004_BAD_LOSS')
    return count,history,dict(totals)


def candidate_rules():
    return {'trained_argmax_050':{'threshold':.50,'compatible':False,'per_source_cap':None},
            **{f'trained_compatible_{int(t*100):03d}':
               {'threshold':t,'compatible':True,'per_source_cap':None} for t in THRESHOLDS}}


def compute_predictions(cases,head,allowed,*,device,old_rules=None,compare=False):
    """All inference decoders consume only frozen anchors and learned logits."""
    outputs={};saved=[]
    rules=candidate_rules()
    base_rule=select.configurations()[0]['argmax_050']
    for case in cases:
        trained_logits=full_logits(head,case['vectors'].to(device)).detach().cpu()
        pred={name:select.score_set(select.choose_edges(case['anchors'],trained_logits,rule,allowed),case['gold'])
              for name,rule in rules.items()}
        if compare:
            pred['g2_0001_argmax_050']=select.score_set(
                select.choose_edges(case['anchors'],case['logits'],base_rule,allowed),case['gold'])
            pred['g2_0001_compatible_050']=select.score_set(
                select.choose_edges(case['anchors'],case['logits'],
                                    select.configurations()[0]['compatible_050'],allowed),case['gold'])
            original={**case,'features':discrim.feature_edges(case,allowed)}
            for name,rule in old_rules.items():
                pred['g2_0003_'+name]=select.score_set(
                    discrim.edges_for_rule(original['features'],rule),case['gold'])
        for name,score in pred.items():
            outputs.setdefault(name,[]).append({**score,'family':case['family']})
        saved.append({'index':case['index'],'family':case['family'],
                      'prompt_sha256':case['prompt_sha256'],'g1_predicted_nodes':len(case['anchors']),
                      'gold_binding_edges':len(case['gold']),'configurations':pred})
    return {name:base.metrics(rows) for name,rows in outputs.items()},saved


def choose_rule(calibration_metrics,baseline):
    """Selection is calibration only; never tune on independent validation."""
    floor=max(.60,baseline['recall']-.15)
    names=tuple(candidate_rules())
    eligible=[name for name in names if calibration_metrics[name]['recall']>=floor]
    if not eligible:
        return None,{'reason':'NO_CANDIDATE_MEETS_CALIBRATION_RECALL_FLOOR',
                     'recall_floor':floor,'eligible_count':0}
    winner=max(eligible,key=lambda name:(calibration_metrics[name]['f1'],
                                        calibration_metrics[name]['precision'],
                                        -calibration_metrics[name]['false_positive'],name))
    return winner,{'reason':'MAX_CALIBRATION_F1_WITH_RECALL_FLOOR',
                   'recall_floor':floor,'eligible_count':len(eligible)}


def validate_original_state(head,original):
    for name,before in original.items():
        if not torch.equal(head.state_dict()[name].detach().cpu(),before):
            raise ValueError('GAMER_G2_0004_FROZEN_PARAMETER_DRIFT:'+name)


def run(*,root,output,g1_validation_report,g2_run,selection_run,discrimination_run,
        device='cuda',max_length=2048,validation_limit=VALIDATION_LIMIT,steps=STEPS):
    root=Path(root);output=Path(output);discrimination_run=Path(discrimination_run)
    if output.exists():raise FileExistsError('GAMER_G2_0004_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,old_cases,upstream,hashes=select.load_parents(
        root,g1_validation_report,g2_run)
    train_rows=base.original_training_rows(dataset,TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(row['prompt']) for row in train_rows))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0004_TRAIN_REPLAY')
    old_dev=base.novel_dev_rows(train_rows,limit=CALIBRATION_LIMIT,seed=base.SEED)
    if _sha_rows(old_dev)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0004_BOOTSTRAP_DEV_REPLAY')
    old_second=select.new_validation_rows(train_rows,old_dev,limit=select.VALIDATION_LIMIT)
    third=discrim.new_validation_rows(train_rows,old_dev,old_second,limit=discrim.VALIDATION_LIMIT)
    reference=json.loads((Path(selection_run)/select.REPORT).read_text(encoding='utf-8'))
    # G2-0003 parent report embeds the G2-0002 exact immutable hashes.
    if (reference.get('schema')!=select.SCHEMA or reference.get('g2_parent_hashes')!=hashes or
            reference.get('validation_dataset_sha256')!=_sha_rows(old_second) or
            reference.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0004_G2_0002_PARENT_DRIFT')
    compare_hashes={**hashes,
        'g2_0002_report_sha256':backbone.sha256(Path(selection_run)/select.REPORT),
        'g2_0002_cases_sha256':backbone.sha256(Path(selection_run)/select.CASES)}
    previous,third_hashes=verify_discrimination_parent(discrimination_run,third,compare_hashes)
    fresh=new_validation_rows(train_rows,old_dev,old_second,third,limit=validation_limit)
    allowed=select.compatibility_from_train(train_rows)
    if reference.get('compatibility_sha256')!=select._hash_json([list(x) for x in sorted(allowed)]):
        raise ValueError('GAMER_G2_0004_COMPATIBILITY_DRIFT')
    head=head.to(device).eval().requires_grad_(False)
    original={name:value.detach().cpu().clone() for name,value in head.state_dict().items()}
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):raise ValueError('GAMER_G2_0004_BACKBONE_UNFROZEN')
    train_cache=capture(head,model,tok,train_rows,device=device,max_length=max_length,group='train')
    calibration=capture(head,model,tok,old_dev,device=device,max_length=max_length,group='calibration')
    independent=capture(head,model,tok,fresh,device=device,max_length=max_length,group='new_independent')
    validate_original_state(head,original)
    # Both arms begin from *exactly* the same frozen G2-0001 parameter state.
    ce_head=deepcopy(head)
    rank_head=deepcopy(head)
    _,ce_history,ce_counts=train(ce_head,train_cache,mode='hardneg_ce',steps=steps,seed=SEED,device=device)
    num_params,rank_history,rank_counts=train(rank_head,train_cache,mode='relation_rank',steps=steps,seed=SEED,device=device)
    for alternative in (ce_head,rank_head):
        validate_original_state(alternative,{k:v for k,v in original.items()
            if not k.startswith(('binding.','pair_projection.'))})
    validate_original_state(head,original)
    old_rules=previous['decoder_rules_calibration_fitted']
    old_baseline=select.configurations()[0]['argmax_050']
    # Determine the old control recall using calibration only.
    baseline_calib=base.metrics([{**select.score_set(
        select.choose_edges(row['anchors'],row['logits'],old_baseline,allowed),row['gold']),
        'family':row['family']} for row in calibration])
    ce_cal,_=compute_predictions(calibration,ce_head,allowed,device=device)
    rank_cal,_=compute_predictions(calibration,rank_head,allowed,device=device)
    ce_choice,ce_choice_info=choose_rule(ce_cal,baseline_calib)
    rank_choice,rank_choice_info=choose_rule(rank_cal,baseline_calib)
    # Independent split is evaluated after both rules are chosen.
    ce_ind,ce_saved=compute_predictions(independent,ce_head,allowed,device=device)
    rank_ind,rank_saved=compute_predictions(independent,rank_head,allowed,device=device,
                                            old_rules=old_rules,compare=True)
    combined={**rank_ind,**{'ce_only_'+key:value for key,value in ce_ind.items()}}
    gates={name:select.validation_gate(rank_ind[name],rank_ind['g2_0001_argmax_050'])
           for name in candidate_rules()}
    winning=rank_ind[rank_choice] if rank_choice else None
    gate=gates[rank_choice] if rank_choice else None
    old_choice=previous['selected_rule']
    comparison={
        'g2_0001_frozen_argmax_050':rank_ind['g2_0001_argmax_050'],
        'g2_0003_frozen_calibration_choice':rank_ind['g2_0003_'+old_choice] if old_choice else None,
        'g2_0004_hardneg_ce_calibration_choice':ce_ind[ce_choice] if ce_choice else None,
        'g2_0004_relation_rank_calibration_choice':winning,
    }
    report={'schema':SCHEMA,'status':'G2_HARD_NEGATIVE_RANKING_DEVELOPMENT_NOT_CERTIFIED',
            'g1_frozen':True,'g1_development_candidate':base.G1_ARM,
            'g2_bootstrap_weights_frozen':True,'g2_0003_fitted_rules_frozen':True,
            'g2_trainable_modules':['pair_projection','binding'],
            'g2_trainable_parameters':num_params,'g2_certified':False,
            'g2_production_unlocked':False,'production_promotion':False,
            'certification_authorized':False,'graph_execution_authorized':False,
            'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
            'gold_used_for_training_and_offline_scoring_only':True,'inference_uses_predicted_g1_only':True,
            'train_cases':len(train_rows),'calibration_cases':len(old_dev),
            'independent_validation_cases':len(fresh),'independent_seed':SEED,
            'independent_dataset_sha256':_sha_rows(fresh),
            'calibration_dataset_sha256':_sha_rows(old_dev),
            'training_steps_per_arm':steps,'batch_size':BATCH,'lr':LR,
            'hard_negatives_per_positive':HARD_NEG_PER_POSITIVE,
            'rank_negative_per_positive':RANK_NEG_PER_POSITIVE,
            'ranking_margin':RANK_MARGIN,'ranking_weight':RANK_WEIGHT,
            'ce_only_last_loss':ce_history[-1],'relation_rank_last_loss':rank_history[-1],
            'ce_only_mining':ce_counts,'relation_rank_mining':rank_counts,
            'upstream_hashes':{**hashes,**third_hashes},
            'g2_0003_original_selected_rule':previous['selected_rule'],
            'g2_0003_independent_screen_pass':previous['candidate_discrimination_screen_pass'],
            'calibration_baseline_metrics':baseline_calib,
            'ce_only_calibration_metrics':ce_cal,'rank_calibration_metrics':rank_cal,
            'ce_only_selected_rule':ce_choice,'ce_only_calibration_selection':ce_choice_info,
            'rank_selected_rule':rank_choice,'rank_calibration_selection':rank_choice_info,
            'independent_validation_metrics':combined,
            'primary_comparison':comparison,
            'independent_screen_checks_by_rule':gates,
            'selected_independent_screen_pass':bool(gate and all(gate.values())),
            'beats_g2_0001_f1':bool(winning and winning['f1']>rank_ind['g2_0001_argmax_050']['f1']),
            'beats_g2_0003_original_choice_f1':bool(winning and old_choice and
                winning['f1']>rank_ind['g2_0003_'+old_choice]['f1']),
            'beats_hardneg_ce_only_f1':bool(winning and ce_choice and
                winning['f1']>ce_ind[ce_choice]['f1']),
            'g1_head_sha256':manifest['head_sha256']}
    output.mkdir(parents=True)
    save_file({name:value.detach().cpu().contiguous() for name,value in base._binding_snapshot(rank_head).items()},
              str(output/WEIGHTS))
    report['development_weights_sha256']=backbone.sha256(output/WEIGHTS)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl([
        {'group':'new_independent_validation',**row,
         'configurations':{**row['configurations'],
                           **{'ce_only_'+k:v for k,v in ce_saved[index]['configurations'].items()}}}
        for index,row in enumerate(rank_saved)]))
    return report
