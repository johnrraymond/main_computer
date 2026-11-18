"""G2-0040: admission-aware routing and bounded graph-completion bake-off.

G1/G2-0031, G2-0032, G2-0038 and G2-0039 weights remain frozen. The
only trainable experimental arm is a copy of the G2-0033 GraphSetEnergy model.
TRAIN-fit chooses a policy, disjoint TRAIN-audit verifies it; DEV is read only
for evaluation, never policy selection. No promotion or sealed-test access.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
import random
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_recovered_relationship_learning as x39

x38=x39.x38
x37=x39.x37
x36=x39.x36
x35=x39.x35
x34=x39.x34
x33=x39.x33
x32=x39.x32
x31=x39.x31
p30=x39.p30
base=x39.base
backbone=x39.backbone
general16=x39.general16
sha_rows=x39.sha_rows
verify_parent38=x39.verify_parent38
fresh_cohorts=x39.fresh_cohorts

SCHEMA='nanojev.gamer.graph.g2.admission-aware-completion-bakeoff.v2'
REPORT='g2_completion_bakeoff_report.json'
LEDGER='g2_completion_bakeoff_per_case.jsonl.gz'
WEIGHTS='g2_completion_bakeoff_development.safetensors'
POLICIES='g2_completion_bakeoff_frozen_policies.json'
SEED=460540
SOURCE_SEED=460333  # sole TRAIN-admitted relationship model in 0039
SOURCE=f'occurrence_graph_s{SOURCE_SEED}'
FRESH=('new_independent_dev_0040','new_topology_dev_0040')
REPLAY=x39.FRESH
TRAIN_STEPS=160
TRAIN_CASES=180
FIT_CASES=80
AUDIT_CASES=80
TARGET_EXPANDED_TRAIN=64
TARGET_EXPANDED_FIT=32
MIN_ANCHOR_RESCUES_TRAIN=12
MIN_ANCHOR_RESCUES_FIT=6
MAX_LENGTH=x39.MAX_LENGTH
# Fixed alternatives; never tune a candidate or pick a winner on DEV.
COUNT_GRID=((.70,.55,2,4.),(.80,.55,3,4.),(.70,.70,3,2.))
SET_GRID=((.0,.0),(.7,0.),(1.4,.30))
ARMS=('cardinality','constrained','adapted')


def verify_parent39(directory,dev39,ancestors,train_sha):
    """Require immutable parent lineage, full per-case metrics and model shapes."""
    folder=Path(directory);path=folder/x39.REPORT
    report=json.loads(path.read_text(encoding='utf-8'))
    checks={'schema':x39.SCHEMA,'status':'RETRAINED_RELATIONSHIP_DEV_NOT_CERTIFIED',
            'parent_hashes':ancestors,'train_dataset_sha256':train_sha,
            'production_promotion':False,'sealed_test_used':False,
            'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
            'development_evaluation_skipped':False,'g2_certified':False,
            'parent_live_replay_passed':True}
    for key,expected in checks.items():
        if report.get(key)!=expected:
            raise ValueError('GAMER_G2_0040_PARENT_0039_CONTRACT:'+key)
    if {k:report['datasets_sha256'].get(k) for k in dev39} != {
            k:x39.sha_rows(v) for k,v in dev39.items()}:
        raise ValueError('GAMER_G2_0040_PARENT_DATASET_DRIFT')
    hashes={}
    for name,key in ((x39.REPORT,'g2_0039_report_sha256'),
                     (x39.LEDGER,'g2_0039_ledger_sha256'),
                     (x39.WEIGHTS,'g2_0039_weights_sha256'),
                     (x39.POLICIES,'g2_0039_policies_sha256')):
        hashes[key]=backbone.sha256(folder/name)
        if name!=x39.REPORT and hashes[key]!=report[key.replace('g2_0039_','')]:
            raise ValueError('GAMER_G2_0040_PARENT_HASH:'+name)
    policies=json.loads((folder/x39.POLICIES).read_text(encoding='utf-8'))
    if policies!={f'relationship_s{s}':report['train_policy_audit'][str(s)]['policy']
                 for s in x39.SEEDS}:
        raise ValueError('GAMER_G2_0040_PARENT_POLICY_DRIFT')
    if not policies[f'relationship_s{SOURCE_SEED}']['enabled'] or any(
        policies[f'relationship_s{s}']['enabled'] for s in x39.SEEDS if s!=SOURCE_SEED):
        raise ValueError('GAMER_G2_0040_PARENT_RELATIONSHIP_POLICY')
    weights=load_file(str(folder/x39.WEIGHTS),device='cpu')
    if any(not torch.isfinite(v).all() for v in weights.values()):
        raise ValueError('GAMER_G2_0040_PARENT_WEIGHT_NONFINITE')
    previous={k:[] for k in dev39}
    with gzip.open(folder/x39.LEDGER,'rt',encoding='utf-8') as stream:
        for raw in stream:
            entry=json.loads(raw);split=entry['group']
            if split not in previous:raise ValueError('GAMER_G2_0040_PARENT_LEDGER_SPLIT')
            pos=len(previous[split]);rows=dev39[split]
            if pos>=len(rows) or any((entry.get('index')!=pos+1,
                    entry.get('prompt_sha256')!=base._sha(rows[pos]['prompt']),
                    entry.get('family')!=rows[pos]['family'],
                    entry.get('gold_total')!=len(base.gold_binding_edges(rows[pos])))):
                raise ValueError('GAMER_G2_0040_PARENT_CASE_IDENTITY')
            previous[split].append(entry)
    if any(len(previous[k])!=len(rows) or p30.aggregate(previous[k])!=report['by_split'][k]
           for k,rows in dev39.items()):
        raise ValueError('GAMER_G2_0040_PARENT_METRIC_DRIFT')
    # 0039 stores its fine-tuned relationship networks for all three seeds.
    return policies,weights,previous,hashes


def load_relationship_weights(parent31,tensors):
    nets={};expected=set()
    for seed in x39.SEEDS:
        model=copy.deepcopy(parent31[seed]['occurrence_graph'])
        pref=f'fine_tuned_s{seed}.'
        state={k:tensors[pref+k] for k in model.state_dict()}
        expected.update(pref+k for k in state)
        model.load_state_dict(state,strict=True)
        nets[seed]=model.eval().requires_grad_(False)
    if expected!=set(tensors):raise ValueError('GAMER_G2_0040_PARENT_WEIGHT_KEYS')
    return nets


def admission_route(original,updated,original_anchors,recovered_anchors,policy,*,seed=SOURCE_SEED):
    """A changed candidate graph is the *only* trigger for using new G2 scores."""
    if set(recovered_anchors)==set(original_anchors):
        return original,False
    if not set(original_anchors).issubset(set(recovered_anchors)):
        raise ValueError('GAMER_G2_0040_RECOVERY_REMOVED_ANCHOR')
    if not policy['enabled']:
        return original,False
    return x39.interpolate(original,updated,policy['blend'],seed=seed),True


def configuration(arm,parameters):
    if arm=='cardinality':
        edge_prob,confidence,max_additions,gap=parameters
        return {'enabled':True,'mode':'group_recovery','edge_prob':edge_prob,
                'confidence':confidence,'group_gap':gap,
                'max_additions':max_additions,'offset':0.}
    weight,global_weight=parameters
    return {'enabled':True,'source':SOURCE,'group_weight':weight,
            'global_weight':global_weight,'fallback':None}


def policy_candidates():
    return [(arm,configuration(arm,params))
            for arm in ARMS for params in (COUNT_GRID if arm=='cardinality' else SET_GRID)]


def _capture_candidate_repair_batch(rows, tag, *, include_raw_g1,
                                    capture, state, acceptors, acceptor_policies,
                                    device, max_length, assert_all_frozen):
    """The actual G2-0051/0052 candidate-capture path, callable in integration tests.

    Reuse the SAME frozen G1 hidden-state bundle for accepted-occurrence replay
    and raw-span recovery. Never consult author labels when building candidates.
    """
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as repair51
    if (not rows or any(row.get('split')!='train' or
            row.get('annotation_provenance')!='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
            for row in rows)):
        raise ValueError('GAMER_G2_0051_TRAIN_CAPTURE_ONLY')
    captured=capture(rows,tag)
    if len(captured)!=len(rows):
        raise ValueError('GAMER_G2_0051_CAPTURE_ROW_COUNT')
    for row,record in zip(rows,captured):
        evidence,(prepared,hidden,eligible)=x38.freeze_context_case(
            row,state['head'],state['model'],state['tokenizer'],
            device=device,max_length=max_length)
        spans=x39.chosen_spans(evidence,acceptors,acceptor_policies,SOURCE_SEED)
        if base.predicted_anchors(spans,prepared)!=record['recovered']['anchors']:
            raise ValueError('GAMER_G2_0051_FROZEN_G1_REPLAY_DRIFT')
        record['candidate_repair']=repair51.candidates_for_record(
            record,evidence,state['allowed'])
        if include_raw_g1:
            with torch.inference_mode():
                raw=state['head'](hidden,eligible=eligible,active_stage='G1')
            # Frozen G1 evidence and original token offsets; no gold graph.
            record['g1_raw_interface']={
                'span_logits':raw['span_logits'].detach().float().cpu(),
                'span_valid':raw['span_valid'].detach().bool().cpu(),
                'prepared':prepared,'allowed':state['allowed'],
                'hidden':hidden.detach().to(device='cpu',dtype=torch.float16).contiguous()}
    assert_all_frozen()
    return captured


def build_case(row,state,parent31,relationship_nets,acceptors,acceptor_policies,
               relationship_policy,*,device):
    """All inference completes without reading the author-supplied gold edges."""
    evidence,(prepared,hidden,eligible)=x38.freeze_context_case(
        row,state['head'],state['model'],state['tokenizer'],device=device,
        max_length=MAX_LENGTH)
    original=x36.frozen_g2_case(row,evidence['baseline_spans'],prepared,
                               hidden,eligible,state['head'])
    spans=x39.chosen_spans(evidence,acceptors,acceptor_policies,SOURCE_SEED)
    recovered=(original if spans==evidence['baseline_spans'] else
        x36.frozen_g2_case(row,spans,prepared,hidden,eligible,state['head']))
    frozen,trained=x39.score_pair(recovered,row,state,parent31,relationship_nets,
                                 device=hidden.device)
    routed,changed=admission_route(frozen,trained,original['anchors'],
                                   recovered['anchors'],relationship_policy)
    return {'row':row,'original':original,'recovered':recovered,
            'frozen':frozen,'routed':routed,
            'admitted':set(recovered['anchors'])!=set(original['anchors']),
            'new_scorer_used':changed}


def prepare_record(record,parent_policies,pol32,nets32,nets33,pol33,adapted=None):
    """Precompute predictions before looking up TRAIN labels or DEV gold."""
    item=record['routed'];nodes=len(record['recovered']['anchors'])
    old=x39.decode_scores(record['frozen'],nodes,parent_policies,pol32,nets32,nets33,pol33)
    routed=x39.decode_scores(item,nodes,parent_policies,pol32,nets32,nets33,pol33)
    shortlisted=x35.shortlist_for_frozen_decoder(item)
    feats=x33.set_features(shortlisted,SOURCE,parent_policies[SOURCE],nodes)
    pred=x33.predictions(feats,nets33[SOURCE_SEED])
    adapted_pred=x33.predictions(feats,adapted) if adapted is not None else pred
    count=x32.probabilities(shortlisted,SOURCE,
        parent_policies[SOURCE],nodes,nets32[SOURCE_SEED])
    return {**record,'baseline':old,'routed_decode':routed,
            'features':feats,'structured_pred':pred,'adapted_pred':adapted_pred,
            'count_prediction':count,
            'shortlisted':shortlisted}



def _opportunity_from_gold(record,gold):
    """Read-only feasibility audit after every candidate decision is frozen."""
    endpoints={node for source,target,_ in gold for node in (source,target)}
    original=set(record['original']['anchors'])
    recovered=set(record['recovered']['anchors'])
    candidates=set(record['shortlisted']['edges'])
    parent=baseline_selection(record,'constrained')
    admitted=bool(record['admitted'])
    return {'admitted':admitted,'anchor_representable':endpoints<=recovered,
        'shortlist_representable':gold<=candidates,
        'newly_anchor_representable':admitted and endpoints<=recovered and
            not endpoints<=original,
        'repairable':admitted and gold<=candidates and parent!=gold,
        'parent_exact':parent==gold,'gold_edges':len(gold),
        'shortlisted_gold_edges':len(gold&candidates),
        'unrepresented_gold_edges':len(gold-candidates)}


def reachable_opportunity(record):
    """TRAIN gold audit: never an inference feature or DEV selector."""
    return _opportunity_from_gold(record,base.gold_binding_edges(record['row']))


def opportunity_summary(records):
    grouped={k:Counter() for k in ('all','unchanged','expanded','expanded_reachable',
                                     'expanded_unreachable','anchor_rescued')}
    for record in records:
        info=record.get('opportunity') or reachable_opportunity(record)
        groups=['all', 'expanded' if info['admitted'] else 'unchanged']
        if info['admitted']:
            groups.append('expanded_reachable' if info['shortlist_representable']
                          else 'expanded_unreachable')
            if info['newly_anchor_representable']:groups.append('anchor_rescued')
        for group in groups:
            c=grouped[group];c['cases']+=1
            c['anchor_representable']+=int(info['anchor_representable'])
            c['shortlist_representable']+=int(info['shortlist_representable'])
            c['newly_anchor_representable']+=int(info['newly_anchor_representable'])
            c['repairable']+=int(info['repairable'])
            c['parent_exact']+=int(info['parent_exact'])
            c['missing_candidate_gold_edges']+=info['unrepresented_gold_edges']
    return {k:dict(v) for k,v in grouped.items()}


def _screen_train_row(row,state,acceptors,acceptor_policies,*,device):
    """Cheap TRAIN-only pre-screen, before expensive G2 pair scoring.

    All candidate admissions are label-blind. TRAIN gold appears only in
    stratification metadata after admissions have been frozen.
    """
    evidence,(prepared,hidden,eligible)=x38.freeze_context_case(
        row,state['head'],state['model'],state['tokenizer'],device=device,
        max_length=MAX_LENGTH)
    spans=x39.chosen_spans(evidence,acceptors,acceptor_policies,SOURCE_SEED)
    original=set(evidence['baseline_anchors'])
    selected=set(base.predicted_anchors(spans,prepared))
    if not original<=selected:raise ValueError('GAMER_G2_0040_MINE_REMOVED_ANCHOR')
    admitted=selected!=original
    gold=base.gold_binding_edges(row)
    endpoints={n for left,right,_ in gold for n in (left,right)}
    return {'admitted':admitted,'gold_anchor_rescue':bool(admitted and
            endpoints<=selected and not endpoints<=original),
            'new_gold_anchors':len((selected-original)&endpoints),
            'false_new_anchors':len((selected-original)-endpoints)}


def mine_enriched_train(rows,state,acceptors,acceptor_policies,*,tag,size,
                        expanded_target,min_rescues,device,
                        screen=_screen_train_row):
    """Mine disjoint TRAIN partitions; require genuine recovered opportunity.

    Selection reads TRAIN gold only AFTER frozen inference. No rejection
    sampling from independent audit or any DEV cohort is permitted.
    """
    if not (0<expanded_target<size and 0<min_rescues<=expanded_target):
        raise ValueError('GAMER_G2_0040_INVALID_MINE_QUOTAS')
    rescued=[];expanded=[];controls=[];statistics=Counter()
    seen=set()
    for row in rows:
        digest=base._sha(row['prompt'])
        if digest in seen:raise ValueError('GAMER_G2_0040_DUPLICATE_MINE_INPUT')
        seen.add(digest)
        if row['split']!='train':raise ValueError('GAMER_G2_0040_MINE_DEV_FORBIDDEN')
        info=screen(row,state,acceptors,acceptor_policies,device=device)
        statistics['screened']+=1
        statistics['admitted']+=int(info['admitted'])
        statistics['anchor_rescued']+=int(info['gold_anchor_rescue'])
        statistics['new_gold_anchors']+=info['new_gold_anchors']
        statistics['false_new_anchors']+=info['false_new_anchors']
        if info['admitted']:
            (rescued if info['gold_anchor_rescue'] else expanded).append(row)
        else:controls.append(row)
        if (len(rescued)>=min_rescues and len(rescued)+len(expanded)>=expanded_target
            and len(controls)>=size-expanded_target):break
    if (len(rescued)<min_rescues or len(rescued)+len(expanded)<expanded_target
        or len(controls)<size-expanded_target):
        raise ValueError('GAMER_G2_0040_INSUFFICIENT_RECOVERY_OPPORTUNITIES:'+tag+
            ':'+str(dict(statistics)))
    # Retain hard expanded negatives as well as newly representable graphs.
    target_rescues=min(len(rescued),max(min_rescues,round(.70*expanded_target)))
    selected_expanded=[*rescued[:target_rescues],
                       *expanded[:expanded_target-target_rescues]]
    if len(selected_expanded)<expanded_target:
        selected_expanded.extend(rescued[target_rescues:
            target_rescues+expanded_target-len(selected_expanded)])
    chosen=[*selected_expanded,*controls[:size-expanded_target]]
    if len(chosen)!=size or len({base._sha(r['prompt']) for r in chosen})!=size:
        raise ValueError('GAMER_G2_0040_MINED_COHORT_INVALID')
    random.Random(SEED+len(tag)).shuffle(chosen)
    proof={**statistics,'selected':size,'selected_expanded':expanded_target,
           'selected_anchor_rescued':len(set(map(lambda r:base._sha(r['prompt']),selected_expanded)) &
            set(map(lambda r:base._sha(r['prompt']),rescued))),
           'selected_unchanged':size-expanded_target,
           'source_pool':len(rows),'selected_sha256':sha_rows(chosen),
           'train_labels_for_mining_only':True}
    print(f'GAMER_G2_0040_MINE {tag} screened={statistics["screened"]}/{len(rows)} '
          f'expanded={expanded_target} anchor_rescued={proof["selected_anchor_rescued"]}',
          flush=True)
    return chosen,proof


def select_arm(record,arm,cfg):
    """Never disturb an unchanged graph, even if a completion model disagrees."""
    if not record['admitted']:
        return baseline_selection(record,arm)
    item=record['shortlisted']
    if arm=='cardinality':
        return x32._select_with_prediction(item,record['parent_policy'],
                                            record['count_prediction'],cfg)
    prediction=record['adapted_pred'] if arm=='adapted' else record['structured_pred']
    return x33.decode(item,record['features'],prediction,cfg)


def baseline_selection(record,arm):
    """Every challenger competes against the *same* routed structured set."""
    return record['routed_decode'][0][f'candidate__structured_s{SOURCE_SEED}']


def prepare_for_selection(rec,parent_policies,pol32,nets32,nets33,pol33,adapted):
    prepared=prepare_record(rec,parent_policies,pol32,nets32,nets33,pol33,adapted)
    prepared['parent_policy']=parent_policies[SOURCE]
    return prepared


def metrics_for(record,choice,arm,*,parent=False):
    """TRAIN or post-prediction DEV metrics, never an inference feature."""
    gold=base.gold_binding_edges(record['row'])
    labeled={**record['shortlisted'],'gold':gold,'gold_total':len(gold),
             'candidate_gold':sum(e in gold for e in record['shortlisted']['edges'])}
    result=p30.measure(choice,labeled)
    score=(record['adapted_pred'][0] if arm=='adapted' and not parent else
           record['routed_decode'][1][f'candidate__structured_s{SOURCE_SEED}'])
    competing,errors=p30.ranking_counts(labeled,score)
    return {**result,'competing_gold':competing,'ranking_failures':errors}


def summarize_policy(records,arm,cfg):
    totals=Counter();baseline=Counter();gains=losses=0
    by_frontier={k:Counter() for k in ('expanded_reachable','expanded_unreachable','unchanged')}
    for record in records:
        before=metrics_for(record,baseline_selection(record,arm),arm,parent=True)
        after=metrics_for(record,select_arm(record,arm,cfg),arm)
        baseline.update(before);totals.update(after)
        gains+=int(bool(after['exact_cases']) and not bool(before['exact_cases']))
        losses+=int(bool(before['exact_cases']) and not bool(after['exact_cases']))
        info=record.get('opportunity') or reachable_opportunity(record)
        cohort=('expanded_reachable' if info['shortlist_representable'] else
                'expanded_unreachable') if info['admitted'] else 'unchanged'
        bucket=by_frontier[cohort]
        bucket['cases']+=1
        bucket['new_exact']+=int(bool(after['exact_cases']) and not bool(before['exact_cases']))
        bucket['lost_exact']+=int(bool(before['exact_cases']) and not bool(after['exact_cases']))
        bucket['exact']+=int(bool(after['exact_cases']))
        bucket['parent_exact']+=int(bool(before['exact_cases']))
    frontier=opportunity_summary(records)
    return {'cases':len(records),'opportunity_frontier':frontier,
            'transitions_by_frontier':{k:dict(v) for k,v in by_frontier.items()},
            'new_exact':gains,'lost_exact':losses,
            'exact':totals['exact_cases'],'parent_exact':baseline['exact_cases'],
            'f1':p30._totals_f1(totals),'parent_f1':p30._totals_f1(baseline),
            'fp':totals['false_positive'],'parent_fp':baseline['false_positive'],
            'rank_errors':totals['ranking_failures'],
            'parent_rank_errors':baseline['ranking_failures']}


def fit_bakeoff(fit,audit):
    """TRAIN-fit selects exactly one winner; independent TRAIN-audit may veto it.

    Never search audit outcomes for an alternative winner. Audit is a
    confirmation, not another data-dependent policy-selection step.
    """
    candidates=[];ledger=[]
    for ordinal,(arm,cfg) in enumerate(policy_candidates()):
        fm=summarize_policy(fit,arm,cfg)
        eligible=(fm['new_exact']>0 and fm['lost_exact']==0 and
                  fm['f1']>=fm['parent_f1'] and fm['fp']<=fm['parent_fp'] and
                  fm['rank_errors']<=fm['parent_rank_errors'])
        record={'arm':arm,'config':cfg,'fit':fm,'eligible_fit':eligible,
                'audit':None,'eligible_audit':False}
        ledger.append(record)
        if eligible:
            score=(fm['new_exact'],fm['f1']-fm['parent_f1'],
                   -fm['fp'], -ordinal)
            candidates.append((score,record))
    if not candidates:
        return {'enabled':False,'arm':None,'config':None,
                'disabled_reason':'no_safe_train_fit_winner'},ledger
    winner=max(candidates,key=lambda p:p[0])[1]
    am=summarize_policy(audit,winner['arm'],winner['config'])
    winner['audit']=am
    winner['eligible_audit']=(am['lost_exact']==0 and
        am['f1']>=am['parent_f1'] and am['fp']<=am['parent_fp'] and
        am['rank_errors']<=am['parent_rank_errors'])
    if not winner['eligible_audit']:
        return {'enabled':False,'arm':None,'config':None,
                'disabled_reason':'train_fit_winner_failed_independent_audit'},ledger
    return {'enabled':True,'arm':winner['arm'],'config':winner['config']},ledger


def fine_tune_decoder(cases,parent,*,steps=TRAIN_STEPS,seed=SEED):
    """Fine-tune a *copy* of the G2-0033 structured model, mixing graph types."""
    if not 1<=steps<=TRAIN_STEPS:raise ValueError('GAMER_G2_0040_TRAIN_STEPS')
    original=[r for r in cases if not r['admitted']]
    expanded=[r for r in cases if r['admitted']]
    if len(original)<8 or len(expanded)<8:
        raise ValueError('GAMER_G2_0040_INSUFFICIENT_PAIRED_TRAIN_GRAPHS')
    model=copy.deepcopy(parent).train().requires_grad_(True)
    rng=random.Random(seed);torch.manual_seed(seed)
    optimizer=torch.optim.AdamW(model.parameters(),lr=7e-5,weight_decay=1e-4)
    parent_state={k:v.clone() for k,v in parent.state_dict().items()}
    for step in range(1,steps+1):
        optimizer.zero_grad(set_to_none=True);losses=[]
        for record in (original[rng.randrange(len(original))],
                       expanded[rng.randrange(len(expanded))]):
            item=record['shortlisted']
            features=record['features']
            labels=np.array([e in base.gold_binding_edges(record['row'])
                             for e in item['edges']],dtype=np.float32)
            outputs=model(*x33._tensor_features(features,'cpu'))
            loss,_=x33.set_objective(outputs,{'index':x33._tensor_features(features,'cpu')[2],
                      'edges':features['edges']},labels)
            losses.append(loss)
        objective=torch.stack(losses).mean()
        if not torch.isfinite(objective):raise ValueError('GAMER_G2_0040_NONFINITE_TRAIN_LOSS')
        objective.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),2.)
        optimizer.step()
        if step==1 or step%40==0 or step==steps:
            print(f'GAMER_G2_0040_TRAIN {step}/{steps} loss={float(objective.detach()):.5f}',flush=True)
    if any(not torch.equal(v,parent_state[k]) for k,v in parent.state_dict().items()):
        raise ValueError('GAMER_G2_0040_PARENT_G2_0033_MUTATED')
    return model.eval().requires_grad_(False),{'original_cases':len(original),
        'expanded_cases':len(expanded),'steps':steps,
        'last_loss':float(objective.detach())}


def evaluation_record(record,policy,group,index,*,parent39=None):
    """Compute all predictions before reading case gold. Audit both decoders."""
    metrics={};predictions={}
    for name,decoded in (('contextual',record['baseline']),('routed',record['routed_decode'])):
        selects,rank,item=decoded
        for mode in ('group','structured'):
            namekey=f'{name}__{mode}_s{SOURCE_SEED}'
            key=f'candidate__{mode}_s{SOURCE_SEED}'
            predictions[namekey]=selects[key]
    for arm,cfg in policy_candidates():
        predictions[f'bakeoff_{arm}_{policy_candidates().index((arm,cfg))}']=select_arm(record,arm,cfg)
    if policy['enabled']:
        selected=select_arm(record,policy['arm'],policy['config'])
    else:selected=baseline_selection(record,'constrained')
    predictions['winner']=selected
    # Labels enter only after every strategy has finished selecting edges.
    gold=base.gold_binding_edges(record['row'])
    for name,chosen in predictions.items():
        item=record['baseline'][2] if name.startswith('contextual__') else record['shortlisted']
        labeled={**item,'gold':gold,'gold_total':len(gold),
                 'candidate_gold':sum(e in gold for e in item['edges'])}
        measure=p30.measure(chosen,labeled)
        if name.startswith('contextual__'):
            ranks=record['baseline'][1]
            mode='group' if '__group_' in name else 'structured'
            score=ranks[f'candidate__{mode}_s{SOURCE_SEED}']
        elif name.startswith('bakeoff_adapted_') or (name=='winner' and policy['arm']=='adapted'):
            score=record['adapted_pred'][0]
        else:
            mode=('group' if name.startswith('bakeoff_cardinality_') or
                  (name=='winner' and policy['arm']=='cardinality') or '__group_' in name
                  else 'structured')
            score=record['routed_decode'][1][f'candidate__{mode}_s{SOURCE_SEED}']
        competing,failed=p30.ranking_counts(labeled,score)
        metrics[name]={**measure,'competing_gold':competing,'ranking_failures':failed}
    ans={'group':group,'index':index,'prompt_sha256':base._sha(record['row']['prompt']),
         'family':record['row']['family'],'gold_total':len(gold),
         'admitted':record['admitted'],'new_scorer_used':record['new_scorer_used'],
         'opportunity':_opportunity_from_gold(record,gold),'metrics':metrics}
    if parent39 is not None:
        key=f'contextual__structured_s{SOURCE_SEED}'
        if parent39['metrics'].get(key)!=metrics[key]:
            raise ValueError('GAMER_G2_0040_PARENT_0039_LIVE_REPLAY')
    return ans


def transitions(rows,before,after):
    gains=losses=unchanged=0
    for record in rows:
        old=bool(record['metrics'][before]['exact_cases'])
        new=bool(record['metrics'][after]['exact_cases'])
        gains+=int(new and not old);losses+=int(old and not new)
        unchanged+=int(old and new)
    return {'newly_exact':gains,'lost_exact':losses,'retained_exact':unchanged}


def persist(output,report,model,policy,rows):
    dest=Path(output);dest.mkdir(parents=True,exist_ok=False)
    x32._write_ledger(dest/LEDGER,rows)
    report['ledger_sha256']=backbone.sha256(dest/LEDGER)
    weights={f'adapted_set.{k}':v.detach().cpu().contiguous()
             for k,v in model.state_dict().items()}
    save_file(weights,str(dest/WEIGHTS))
    report['weights_sha256']=backbone.sha256(dest/WEIGHTS)
    (dest/POLICIES).write_text(json.dumps(policy,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    report['policies_sha256']=backbone.sha256(dest/POLICIES)
    (dest/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                             encoding='utf-8')
    return report


def fresh_cohorts40(prior,pool37,dev37,pool38,dev38,pool39,dev39):
    # Earlier histories legitimately replay the same case; deduplicate only the
    # inherited history, then demand disjoint new TRAIN and DEV cohorts.
    inherited=list({base._sha(r['prompt']):r for r in prior}.values())
    all_rows=[*inherited,*(r for parts in (pool37,pool38,pool39) for batch in parts for r in batch),
              *(r for splits in (dev37,dev38,dev39) for rows in splits.values() for r in rows)]
    if len({base._sha(r['prompt']) for r in all_rows})!=len(all_rows):
        raise ValueError('GAMER_G2_0040_PRIOR_COHORT_OVERLAP')
    pool=x37.novel_train_pool(all_rows,seed=SEED)
    withheld=[*all_rows,*(r for batch in pool for r in batch)]
    independent=general16.independent_rows((withheld,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((withheld,independent),limit=128,seed=SEED+101)
    if any(r['split']!='train' for batch in pool for r in batch) or any(
        r['split']!='dev' for r in (*independent,*topology)) or len({
        base._sha(r['prompt']) for r in (*withheld,*independent,*topology)})!=len(withheld)+len(independent)+len(topology):
        raise ValueError('GAMER_G2_0040_COHORT_LEAKAGE')
    return pool,dict(zip(FRESH,(independent,topology)))


def run(*,precision30_run,representation31_run,completeness32_run,structured33_run,
        diagnosis34_run,coverage35_run,selective36_run,curriculum37_run,context38_run,
        relationship39_run,output,device='cuda',max_length=MAX_LENGTH,
        diagnostic_callback=None,**kwargs):
    if max_length!=MAX_LENGTH:raise ValueError('GAMER_G2_0040_FIXED_PROTOCOL')
    if Path(output).exists():raise FileExistsError('GAMER_G2_0040_IMMUTABLE_OUTPUT')
    state=p30._prepare(output=output,device=device,max_length=max_length,
                       steps=p30.STEPS,**kwargs)
    cal30,dev30=p30.calibration_and_dev(state['history29'],state['fresh29'])
    _,pol30,_,hash30=x31.verify_parent30(precision30_run,state['ancestors'],
        sha_rows(state['training']),cal30,{**state['fresh29'],**dev30})
    cal31,dev31=x31.fresh_cohorts(state,cal30,dev30)
    ancestors31={**state['ancestors'],**hash30}
    _,pol31,_,tensors31,hash31=x32.verify_parent31(
        representation31_run,old_cal=cal30,old_dev=dev30,cal31=cal31,
        dev31=dev31,ancestors=ancestors31,training_sha=sha_rows(state['training']))
    (train32,fit32,audit32),dev32=x32.fresh_cohorts(state,cal30,dev30,cal31,dev31)
    ancestors32={**ancestors31,**hash31}
    _,pol32,_,tensors32,hash32=x33.verify_parent32(completeness32_run,
        train32=train32,fit32=fit32,audit32=audit32,
        dev32={**dev31,**dev32},ancestors=ancestors32,
        training_sha=sha_rows(state['training']))
    (train33,fit33,audit33),dev33=x33.fresh_cohorts(state,cal30,dev30,cal31,
        dev31,train32,fit32,audit32,dev32)
    ancestors33={**ancestors32,**hash32}
    _,pol33,_,tensors33,hash33=x34.verify_parent33(structured33_run,
        train33=train33,fit33=fit33,audit33=audit33,
        old_dev=dev32,new_dev=dev33,ancestors=ancestors33,
        training_sha=sha_rows(state['training']))
    dev34=x34.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33)
    ancestors34={**ancestors33,**hash33}
    _,hash34=x35.verify_parent34(diagnosis34_run,ancestors=ancestors34,
        train_sha=sha_rows(state['training']),old_dev=dev33,fresh34=dev34)
    dev35=x35.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33,dev34)
    ancestors35={**ancestors34,**hash34}
    _,hash35=x36.verify_parent35(coverage35_run,ancestors=ancestors35,
        train_sha=sha_rows(state['training']),old_dev=dev34,fresh35=dev35)
    (train36,fit36,audit36),dev36=x36.fresh_cohorts(state,cal30,dev30,cal31,
        dev31,train32,fit32,audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35)
    ancestors36={**ancestors35,**hash35}
    _,hash36=x37.verify_parent36(selective36_run,ancestors=ancestors36,
        train_sha=sha_rows(state['training']),train36=train36,fit36=fit36,
        audit36=audit36,old_dev=dev35,fresh36=dev36)
    pool37,dev37=x37.fresh_cohorts(state,cal30,dev30,cal31,dev31,train32,fit32,
        audit32,dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    ancestors37={**ancestors36,**hash36}
    _,hash37=x38.verify_parent37(curriculum37_run,ancestors=ancestors37,
        train_sha=sha_rows(state['training']),fresh37=dev37,pool37=pool37)
    prior=x37._previous_rows(state,cal30,dev30,cal31,dev31,train32,fit32,audit32,
        dev32,train33,fit33,audit33,dev33,dev34,dev35,
        train36,fit36,audit36,dev36)
    pool38,dev38=x38.fresh_cohorts(prior,pool37,dev37)
    ancestors38={**ancestors37,**hash37}
    parent38,acceptor_policies,acceptors,hash38=verify_parent38(
        context38_run,ancestors=ancestors38,train_sha=sha_rows(state['training']),
        pool38=pool38,dev38={**dev37,**dev38})
    train_pool,fresh=fresh_cohorts(prior,pool37,dev37,pool38,dev38)
    parent31=x32.load_parent_models(tensors31,device='cpu')
    nets32=x33.load_parent32_models(tensors32,device='cpu')
    nets33=x34.load_parent33_models(tensors33,device='cpu')
    parent_policies={'calibrated_reference_0030':pol30['calibrated_reference'],
        'calibrated_reference_0031':pol31['calibrated_reference'],
        'predicate_argument_0030':pol30[x31.CHAMPION],
        **{k:pol31[k] for k in x32.SOURCE_NAMES}}
    ancestors39={**ancestors38,**hash38}
    rel_policy,relation_tensors,parent39,hash39=verify_parent39(
        relationship39_run,{**dev38,**fresh},ancestors39,
        x39.sha_rows(state['training']))
    relation_nets=load_relationship_weights(parent31,relation_tensors)
    pool40,dev40=fresh_cohorts40(prior,pool37,dev37,pool38,dev38,train_pool,fresh)
    # Mine only disjoint TRAIN partitions; independent TRAIN-audit stays natural.
    audit40=pool40[2][:AUDIT_CASES]
    if len(audit40)<AUDIT_CASES:
        raise ValueError('GAMER_G2_0040_SHORT_NATURAL_AUDIT')
    parent_snap={s:{k:v.detach().clone() for k,v in parent31[s]['occurrence_graph'].state_dict().items()} for s in x39.SEEDS}
    acceptor_snap={s:{k:v.detach().clone() for k,v in acceptors[s].state_dict().items()} for s in x39.SEEDS}
    frozen_modules={'g2_0033':{s:nets33[s] for s in x39.SEEDS},
        'g2_0032_group':{s:nets32[s][0] for s in x39.SEEDS},
        'g2_0032_graph':{s:nets32[s][1] for s in x39.SEEDS},
        'g2_0039':{s:relation_nets[s] for s in x39.SEEDS}}
    frozen_snap={group:{seed:{k:v.detach().clone() for k,v in model.state_dict().items()}
        for seed,model in network.items()} for group,network in frozen_modules.items()}
    def assert_all_frozen():
        x39.assert_frozen(state,parent31,acceptors,parent_snap,acceptor_snap)
        for group,networks in frozen_modules.items():
            for seed,model in networks.items():
                if any(not torch.equal(v,model.state_dict()[k])
                       for k,v in frozen_snap[group][seed].items()):
                    raise ValueError('GAMER_G2_0040_FROZEN_PARENT_MUTATED:'+group)
    rpolicy=rel_policy[f'relationship_s{SOURCE_SEED}']
    def capture(rows,tag):
        cases=[]
        for ix,row in enumerate(rows,1):
            record=build_case(row,state,parent31,relation_nets,acceptors,
                acceptor_policies,rpolicy,device=device)
            prepared=prepare_for_selection(record,parent_policies,pol32,nets32,nets33,
                                           pol33,adapted=None)
            cases.append(prepared)
            if ix==1 or ix%40==0 or ix==len(rows):
                print(f'GAMER_G2_0040_CAPTURE {tag} {ix}/{len(rows)}',flush=True)
        return cases
    train40,train_mining=mine_enriched_train(pool40[0],state,acceptors,
        acceptor_policies,tag='train_0040',size=TRAIN_CASES,
        expanded_target=TARGET_EXPANDED_TRAIN,
        min_rescues=MIN_ANCHOR_RESCUES_TRAIN,device=device)
    fit40,fit_mining=mine_enriched_train(pool40[1],state,acceptors,
        acceptor_policies,tag='train_fit_0040',size=FIT_CASES,
        expanded_target=TARGET_EXPANDED_FIT,
        min_rescues=MIN_ANCHOR_RESCUES_FIT,device=device)
    training=capture(train40,'train_0040')
    fitting=capture(fit40,'train_fit_0040')
    auditing=capture(audit40,'train_audit_0040')
    # TRAIN gold audits run only after all label-blind selections are complete.
    for ex in (*training,*fitting,*auditing):
        ex['opportunity']=reachable_opportunity(ex)
    training_frontier=opportunity_summary(training)
    fit_frontier=opportunity_summary(fitting)
    natural_audit_frontier=opportunity_summary(auditing)
    print('GAMER_G2_0040_FRONTIER '+json.dumps({
        'train':training_frontier,'fit':fit_frontier,
        'natural_audit':natural_audit_frontier},sort_keys=True),flush=True)
    if diagnostic_callback is not None:
        # TRAIN-only callbacks may request a DEV capture only after their
        # candidate has passed TRAIN-fit and independent TRAIN-audit.
        # DEV capture is label-blind; gold metrics belong to the caller.
        def post_selection_dev_capture():
            evaluated={}
            for split,rows in {**fresh,**dev40}.items():
                cases=[]
                for ix,row in enumerate(rows,1):
                    record=build_case(row,state,parent31,relation_nets,acceptors,
                        acceptor_policies,rpolicy,device=device)
                    cases.append(prepare_for_selection(record,parent_policies,pol32,
                        nets32,nets33,pol33,adapted=None))
                    if ix==1 or ix%40==0 or ix==len(rows):
                        print(f'GAMER_G2_0042_CAPTURE_DEV {split} {ix}/{len(rows)}',flush=True)
                evaluated[split]=cases
            assert_all_frozen()
            return evaluated
        def capture_counterfactual_train_rows(rows, tag='g2_0050_counterfactual'):
            # A private diagnostic hook: all new material must be author-annotated
            # TRAIN rows. The capture uses the same frozen Qwen/G1/G2 parents
            # and label-blind candidate generation as the original cohorts.
            if (not rows or any(row.get('split')!='train' or
                    row.get('annotation_provenance')!='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
                    for row in rows)):
                raise ValueError('GAMER_G2_0050_TRAIN_CAPTURE_ONLY')
            captured=capture(rows,tag)
            assert_all_frozen()
            return captured
        def capture_candidate_repair_rows(rows, tag='g2_0051_candidate_repair', *, include_raw_g1=False):
            return _capture_candidate_repair_batch(rows, tag,
                include_raw_g1=include_raw_g1, capture=capture, state=state,
                acceptors=acceptors, acceptor_policies=acceptor_policies,
                device=device, max_length=max_length, assert_all_frozen=assert_all_frozen)
        assert_all_frozen()
        return diagnostic_callback(training=training,fitting=fitting,auditing=auditing,
            training_frontier=training_frontier,fit_frontier=fit_frontier,
            natural_audit_frontier=natural_audit_frontier,train_rows=train40,
            fit_rows=fit40,audit_rows=audit40,
            meta={'train_mining':train_mining,'fit_mining':fit_mining},pol33=pol33,
            parent33_models=nets33,post_selection_dev_capture=post_selection_dev_capture,
            assert_all_frozen=assert_all_frozen,
            capture_counterfactual_train_rows=capture_counterfactual_train_rows,
            capture_candidate_repair_rows=capture_candidate_repair_rows)
    no_opportunity=not fit_frontier['all'].get('repairable')
    if no_opportunity:
        # The decoder cannot fix a graph absent from its candidate inventory.
        # Skip training and write an actionable TRAIN-only ceiling diagnosis.
        adapted=copy.deepcopy(nets33[SOURCE_SEED]).eval().requires_grad_(False)
        training_audit={'original_cases':len(training)-training_frontier['expanded'].get('cases',0),
            'expanded_cases':training_frontier['expanded'].get('cases',0),
            'steps':0,'last_loss':None,'skipped':'no_reachable_train_fit_opportunity'}
        policy={'enabled':False,'arm':None,'config':None,
                'disabled_reason':'no_shortlist_representable_train_fit_opportunities'}
        calibration=[{'arm':arm,'config':cfg,'fit':None,'audit':None,
                      'eligible_fit':False,'eligible_audit':False,
                      'skipped':'no_reachable_train_fit_opportunity'}
                     for arm,cfg in policy_candidates()]
    else:
        adapted,training_audit=fine_tune_decoder(training,nets33[SOURCE_SEED])
        # TRAIN fit/audit get fresh predictions from the *fixed* trained decoder.
        for example in (*fitting,*auditing):
            example['adapted_pred']=x33.predictions(example['features'],adapted)
        policy,calibration=fit_bakeoff(fitting,auditing)

    # Reject mutation of any parent, even if no bake-off policy qualifies.
    assert_all_frozen()
    report={'schema':SCHEMA,'status':'COMPLETION_BAKEOFF_DEV_NOT_CERTIFIED',
        'parent_hashes':{**ancestors39,**hash39},
        'train_dataset_sha256':x39.sha_rows(state['training']),
        'train_pool_sha256':x39.sha_rows([r for batch in pool40 for r in batch]),
        'datasets_sha256':{k:x39.sha_rows(v) for k,v in {**dev38,**fresh,**dev40}.items()},
        'training_sha256':{key:x39.sha_rows(rows) for key,rows in
            zip(('train_0040','train_fit_0040','train_audit_0040'),
                (train40,fit40,audit40))},
        'train_records':{'training':len(training),'fit':len(fitting),
            'audit':len(auditing),'expanded_train':training_audit['expanded_cases'],
            'expanded_fit':fit_frontier['expanded'].get('cases',0),
            'expanded_natural_audit':natural_audit_frontier['expanded'].get('cases',0)},
        'mining':{'train':train_mining,'fit':fit_mining,
                  'audit':{'sampling':'natural_unfiltered','cases':len(auditing)}},
        'opportunity_frontier':{'train':training_frontier,'fit':fit_frontier,
                 'natural_audit':natural_audit_frontier},
        'protocol':{'parent_0039_scorer_frozen':True,'source_seed':SOURCE_SEED,
            'admission_aware_routing':True,'fixed_candidate_grid':len(policy_candidates()),
            'training_steps':TRAIN_STEPS,'count_grid':COUNT_GRID,'set_grid':SET_GRID,
            'disjoint_train_fit_and_audit':True,'no_dev_policy_selection':True,
            'train_and_fit_enriched':True,'audit_natural_distribution':True,
            'mined_expanded_train':TARGET_EXPANDED_TRAIN,
            'mined_expanded_fit':TARGET_EXPANDED_FIT,
            'min_anchor_rescues_train':MIN_ANCHOR_RESCUES_TRAIN,
            'min_anchor_rescues_fit':MIN_ANCHOR_RESCUES_FIT},
        'frozen_g1':True,'frozen_backbone':True,'frozen_g2_0031_parent':True,
        'frozen_g2_0032':True,'frozen_g2_0033_parent':True,
        'frozen_g2_0038_acceptor':True,'frozen_g2_0039_scorer':True,
        'dev_labels_used_for_training':False,'dev_labels_used_for_calibration':False,
        'production_promotion':False,'sealed_test_used':False,'g2_certified':False,
        'train_audit':training_audit,'train_bakeoff':calibration,'train_policy':policy,
        'development_evaluation_skipped':not policy['enabled']}
    if not policy['enabled']:
        report['status']='TRAIN_BAKEOFF_NO_SAFE_WINNER'
        report['by_split']={};report['evaluation']={}
        report['development_gates']={'passed':False,'reason':policy.get(
            'disabled_reason','no_train_fit_audit_winner')}
        return persist(output,report,adapted,policy,[])
    evaluated={}
    for split,rows in {**fresh,**dev40}.items():
        collected=[]
        for ix,row in enumerate(rows,1):
            record=build_case(row,state,parent31,relation_nets,acceptors,
                acceptor_policies,rpolicy,device=device)
            example=prepare_for_selection(record,parent_policies,pol32,nets32,
                nets33,pol33,adapted)
            item=evaluation_record(example,policy,split,ix,
                parent39=parent39[split][ix-1] if split in fresh else None)
            collected.append(item)
            if ix==1 or ix%20==0 or ix==len(rows):
                print(f'GAMER_G2_0040_EVAL {split} {ix}/{len(rows)}',flush=True)
        evaluated[split]=collected
    assert_all_frozen()
    # Final gate is diagnostic only, not a means of choosing another policy.
    new=[r for split in FRESH for r in evaluated[split]]
    replay=[r for split in REPLAY for r in evaluated[split]]
    former='routed__structured_s'+str(SOURCE_SEED)
    moves=transitions(new,former,'winner')
    original_name='contextual__structured_s'+str(SOURCE_SEED)
    original_moves=transitions(new,original_name,'winner')
    replay_moves=transitions(replay,former,'winner')
    replay_original_moves=transitions(replay,original_name,'winner')
    summary=p30.aggregate(new)
    replay_summary=p30.aggregate(replay)
    topology=p30.aggregate(evaluated[FRESH[1]])
    parent=summary['models'][former];winner=summary['models']['winner']
    old_replay=replay_summary['models'][former];new_replay=replay_summary['models']['winner']
    original=summary['models'][original_name]
    original_replay=replay_summary['models'][original_name]
    original_ood=topology['models'][original_name]
    ood_old=topology['models'][former];ood_new=topology['models']['winner']
    passed=all((moves['newly_exact']>0,moves['lost_exact']==0,
        replay_moves['lost_exact']==0,
        original_moves['lost_exact']==0,replay_original_moves['lost_exact']==0,
        original_moves['newly_exact']>0,
        winner['f1']>=parent['f1'],winner['f1']>=original['f1'],
        winner['ranking_failures']<=parent['ranking_failures'],
        new_replay['f1']>=old_replay['f1'],
        new_replay['f1']>=original_replay['f1'],
        new_replay['ranking_failures']<=old_replay['ranking_failures'],
        ood_new['ranking_failures']<=ood_old['ranking_failures'],
        ood_new['f1']>=ood_old['f1'],ood_new['f1']>=original_ood['f1'],
        ood_new['exact_cases']>=ood_old['exact_cases'],
        ood_new['exact_cases']>=original_ood['exact_cases']))
    report['by_split']={name:p30.aggregate(rows) for name,rows in evaluated.items()}
    report['evaluation']={'replayed_0039_dev':replay_summary,'new_0040_dev':summary}
    report['exact_graph_transitions']=moves
    report['original_contextual_exact_graph_transitions']=original_moves
    report['replay_exact_graph_transitions']=replay_moves
    report['replay_original_contextual_exact_graph_transitions']=replay_original_moves
    report['development_gates']={'passed':passed,'new_exact':moves['newly_exact'],
        'lost_exact':moves['lost_exact'],'replay_lost_exact':replay_moves['lost_exact'],
        'original_contextual_lost_exact':original_moves['lost_exact'],
        'original_contextual_new_exact':original_moves['newly_exact'],
        'f1_delta':winner['f1']-parent['f1'],
        'replay_f1_delta':new_replay['f1']-old_replay['f1'],
        'ranking_failure_delta':winner['ranking_failures']-parent['ranking_failures'],
        'replay_ranking_failure_delta':new_replay['ranking_failures']-old_replay['ranking_failures'],
        'topology_f1_delta':ood_new['f1']-ood_old['f1'],
        'topology_ranking_failure_delta':ood_new['ranking_failures']-ood_old['ranking_failures'],
        'topology_exact_delta':ood_new['exact_cases']-ood_old['exact_cases'],
        'production_promotion':False}
    report['parent_live_replay_passed']=True
    return persist(output,report,adapted,policy,
        [r for rows in evaluated.values() for r in rows])
