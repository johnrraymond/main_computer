"""G2-0005: frozen G2-0004 ranking-head density-aware relationship selection.

The validated G1-1.25 and G2-0004 ranking weights do not change. Reinspect
G2-0004's *previously evaluated* independent DEV cohort for calibration, then
measure fixed rules on a new prompt-disjoint DEV cohort. All decoder inputs
come from predicted G1 nodes, frozen scores, and train-only compatibility;
labels are used for offline reporting and calibration only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as prior
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES

SCHEMA='nanojev.gamer.graph.g2.density-aware-frozen-discrimination.v1'
REPORT='g2_density_discrimination_report.json'
CASES='g2_density_discrimination_per_case.jsonl'
AUDIT='g2_density_calibration_audit.json'
SEED=350005
VALIDATION_LIMIT=320
THRESHOLDS=(.45,.50,.55,.60)
DENSITY_PENALTIES=(.15,.30,.50)
COMPETITION_PENALTIES=(0.0,.35)
BASE_RULES=('trained_compatible_050','trained_compatible_060')


def sha_rows(rows):
    return hashlib.sha256(base._jsonl(rows)).hexdigest()


def new_validation_rows(train,first,second,third,fourth,*,limit=VALIDATION_LIMIT,seed=SEED):
    previous=train+first+second+third+fourth
    if limit<2 or limit%2 or not all((train,first,second,third,fourth)):
        raise ValueError('GAMER_G2_0005_BAD_COHORT')
    fresh=base.novel_dev_rows(previous,limit=limit,seed=seed)
    old_hashes={base._sha(r['prompt']) for r in previous}
    new_hashes=[base._sha(r['prompt']) for r in fresh]
    if len(set(new_hashes))!=len(new_hashes) or set(new_hashes)&old_hashes:
        raise ValueError('GAMER_G2_0005_PROMPT_LEAKAGE')
    if Counter(row['family'] for row in fresh)!={'encounter':limit//2,'allocation':limit//2}:
        raise ValueError('GAMER_G2_0005_UNBALANCED_HOLDOUT')
    return fresh


def verify_parent(parent_dir,fourth,head,expected_hashes,allowed):
    """Check saved contract, every case and metric, and exact ranking weights."""
    parent_dir=Path(parent_dir)
    report_path=parent_dir/prior.REPORT
    cases_path=parent_dir/prior.CASES
    weights_path=parent_dir/prior.WEIGHTS
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={
        'schema':prior.SCHEMA,'g1_development_candidate':base.G1_ARM,
        'g1_frozen':True,'g2_bootstrap_weights_frozen':True,
        'g2_0003_fitted_rules_frozen':True,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'certification_authorized':False,'graph_execution_authorized':False,
        'memory_writes':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,
        'gold_used_for_training_and_offline_scoring_only':True,
        'inference_uses_predicted_g1_only':True,
        'selected_independent_screen_pass':False,
        'rank_selected_rule':'trained_compatible_050',
        'independent_validation_cases':len(fourth),
        'independent_seed':prior.SEED,
        'independent_dataset_sha256':sha_rows(fourth),
        'g1_head_sha256':base.G1_HEAD_SHA,
    }
    for name,value in required.items():
        if type(report.get(name)) is not type(value) or report[name]!=value:
            raise ValueError('GAMER_G2_0005_PARENT_CONTRACT:'+name)
    if report.get('upstream_hashes')!=expected_hashes:
        raise ValueError('GAMER_G2_0005_PARENT_LINEAGE')
    if report.get('development_weights_sha256')!=backbone.sha256(weights_path):
        raise ValueError('GAMER_G2_0005_PARENT_WEIGHTS_HASH')
    weights=load_file(str(weights_path),device='cpu')
    expected=base._binding_snapshot(head)
    if set(weights)!=set(expected):raise ValueError('GAMER_G2_0005_PARENT_WEIGHT_KEYS')
    for key,value in weights.items():
        if value.shape!=expected[key].shape or not torch.isfinite(value).all():
            raise ValueError('GAMER_G2_0005_PARENT_WEIGHT_SHAPE:'+key)
    saved=[json.loads(line) for line in cases_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(saved)!=len(fourth):raise ValueError('GAMER_G2_0005_PARENT_CASE_COUNT')
    metrics=report.get('independent_validation_metrics')
    if not isinstance(metrics,dict) or not all(name in metrics for name in
                                       (*BASE_RULES,'g2_0001_argmax_050')):
        raise ValueError('GAMER_G2_0005_PARENT_CONFIGURATIONS')
    for index,(row,old) in enumerate(zip(fourth,saved),1):
        if (old.get('group')!='new_independent_validation' or old.get('index')!=index or
                old.get('family')!=row['family'] or
                old.get('prompt_sha256')!=base._sha(row['prompt']) or
                old.get('gold_binding_edges')!=len(base.gold_binding_edges(row)) or
                set(old.get('configurations',{}))!=set(metrics)):
            raise ValueError('GAMER_G2_0005_PARENT_CASE_IDENTITY')
    recomputed={name:base.metrics([{**row['configurations'][name],'family':row['family']} for row in saved])
                for name in metrics}
    if recomputed!=metrics:raise ValueError('GAMER_G2_0005_PARENT_METRICS')
    if report.get('rank_calibration_metrics',{}).get('trained_compatible_050') is None:
        raise ValueError('GAMER_G2_0005_PARENT_CALIBRATION')
    original={key:value.detach().cpu().clone() for key,value in head.state_dict().items()
              if key not in weights}
    head.load_state_dict(weights,strict=False)
    prior.validate_original_state(head,original)
    return report,saved,{
        'g2_0004_report_sha256':backbone.sha256(report_path),
        'g2_0004_cases_sha256':backbone.sha256(cases_path),
        'g2_0004_weights_sha256':backbone.sha256(weights_path),
    }


def decoder_rules():
    result={}
    for threshold in THRESHOLDS:
        for density in DENSITY_PENALTIES:
            for competition in COMPETITION_PENALTIES:
                label=(f'density_p{int(round(threshold*100)):02d}'
                       f'_d{int(round(density*100)):02d}'
                       f'_c{int(round(competition*100)):02d}')
                result[label]={'minimum_confidence':threshold,
                               'density_penalty':density,
                               'competition_penalty':competition}
    return result


def _bucket(n):
    return 'small_1_24' if n<=24 else 'medium_25_48' if n<=48 else 'crowded_49_128'


def candidates(anchors,logits,allowed):
    """Compute confidence, source density, and competing evidence with no oracle.

    Positive-class argmax is mandatory. Candidate degree counts *compatible*
    positive-class destinations for each predicted source and relationship.
    The peak competitor is the strongest *other destination* in that group.
    """
    n=len(anchors)
    if n<1 or n>128 or logits.shape!=(n,n,len(BIND_CLASSES)):
        raise ValueError('GAMER_G2_0005_BAD_PAIR_SHAPE')
    if not torch.isfinite(logits).all():raise ValueError('GAMER_G2_0005_NONFINITE_LOGITS')
    probs=logits.detach().float().cpu().softmax(-1)
    grouped=defaultdict(list)
    for i,src in enumerate(anchors):
        for j,dst in enumerate(anchors):
            if i==j:continue
            k=int(probs[i,j].argmax())
            if k==0:continue
            rel=BIND_CLASSES[k]
            if (rel,src[2],dst[2]) not in allowed:continue
            conf=float(probs[i,j,k])
            # Maintain the complete positive-class candidate inventory; the
            # base probability cut is applied per candidate at decode time.
            conf=min(1.-1.e-8,max(1.e-8,conf))
            odds=math.log(conf/(1.-conf))
            grouped[(i,rel)].append((i,j,k,conf,odds))
    features=[]
    for key,items in grouped.items():
        count=len(items)
        peak=max(x[4] for x in items)
        for i,j,k,conf,odds in items:
            features.append((anchors[i],anchors[j],BIND_CLASSES[k],conf,odds,
                             count,max(0.,peak-odds)))
    return features


def select_edges(features,rule):
    if set(rule)!={'minimum_confidence','density_penalty','competition_penalty'}:
        raise ValueError('GAMER_G2_0005_BAD_RULE_KEYS')
    t=rule['minimum_confidence'];alpha=rule['density_penalty'];beta=rule['competition_penalty']
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (t,alpha,beta)) or not 0<t<1 or alpha<0 or beta<0:
        raise ValueError('GAMER_G2_0005_BAD_RULE_VALUE')
    minimum=math.log(t/(1-t))
    result=set()
    for src,dst,rel,conf,odds,count,delta in features:
        # Continuous source-and-relation density pressure. No fixed binding
        # count and no cutoff fitted with validation labels.
        adjusted=odds-alpha*math.log1p(count-1)-beta*delta
        if adjusted>=minimum:
            result.add((src,dst,rel))
            if len(result)>MAX_EDGES:raise ValueError('GAMER_G2_0005_EDGE_LIMIT')
    return result


def replay_rank_parent(cached,saved,head,allowed,*,device):
    """Check actual G2-0004 CUDA predictions against its saved DEV records."""
    if len(cached)!=len(saved):raise ValueError('GAMER_G2_0005_REPLAY_CASE_COUNT')
    rules=prior.candidate_rules()
    for item,old in zip(cached,saved):
        if (item['index']!=old['index'] or item['family']!=old['family'] or
                item['prompt_sha256']!=old['prompt_sha256'] or
                len(item['anchors'])!=old['g1_predicted_nodes'] or
                len(item['gold'])!=old['gold_binding_edges']):
            raise ValueError('GAMER_G2_0005_REPLAY_IDENTITY')
        rank_logits=prior.full_logits(head,item['vectors'].to(device)).detach().cpu()
        for name,rule in rules.items():
            actual=sel.score_set(sel.choose_edges(item['anchors'],rank_logits,rule,allowed),item['gold'])
            if old['configurations'][name]!=actual:
                raise ValueError('GAMER_G2_0005_REPLAY_PREDICTIONS:'+name)
        old_base=sel.score_set(sel.choose_edges(item['anchors'],item['logits'],
                       sel.configurations()[0]['argmax_050'],allowed),item['gold'])
        if old['configurations']['g2_0001_argmax_050']!=old_base:
            raise ValueError('GAMER_G2_0005_REPLAY_BOOTSTRAP')


def evaluate(cached,head,allowed,*,device,group,include_diagnostics=False):
    rules=decoder_rules();out=defaultdict(list);saved=[];audit=defaultdict(lambda:defaultdict(list))
    for item in cached:
        rank_logits=prior.full_logits(head,item['vectors'].to(device)).detach().cpu()
        features=candidates(item['anchors'],rank_logits,allowed)
        predictions={name:sel.score_set(select_edges(features,rule),item['gold'])
                     for name,rule in rules.items()}
        for name in BASE_RULES:
            predictions[name]=sel.score_set(sel.choose_edges(item['anchors'],rank_logits,
                                      prior.candidate_rules()[name],allowed),item['gold'])
        predictions['g2_0001_argmax_050']=sel.score_set(sel.choose_edges(item['anchors'],item['logits'],
                            sel.configurations()[0]['argmax_050'],allowed),item['gold'])
        predictions['g2_0001_compatible_050']=sel.score_set(sel.choose_edges(item['anchors'],item['logits'],
                            sel.configurations()[0]['compatible_050'],allowed),item['gold'])
        bucket=_bucket(len(item['anchors']))
        for name,row in predictions.items():
            out[name].append({**row,'family':item['family']})
        if include_diagnostics:
            for src,dst,rel,conf,odds,count,delta in features:
                status='correct' if (src,dst,rel) in item['gold'] else 'false'
                audit[bucket][status].append((conf,count,delta))
        saved.append({'group':group,'index':item['index'],'family':item['family'],
                      'prompt_sha256':item['prompt_sha256'],
                      'g1_predicted_nodes':len(item['anchors']),
                      'density_bucket':bucket,'gold_binding_edges':len(item['gold']),
                      'configurations':predictions})
    return {name:base.metrics(values) for name,values in out.items()},saved,audit


def choose_rule(results,baseline):
    """Screen solely on previously inspected calibration, never fresh DEV."""
    floor=max(.60,baseline['recall']-.15)
    eligible=[name for name in decoder_rules() if results[name]['recall']>=floor]
    if not eligible:return None,{'recall_floor':floor,'eligible_count':0,'reason':'NO_RULE_MEETS_RECALL_FLOOR'}
    winner=max(eligible,key=lambda name:(results[name]['f1'],results[name]['precision'],
                                         -results[name]['false_positive'],name))
    return winner,{'recall_floor':floor,'eligible_count':len(eligible),
                   'reason':'MAX_CALIBRATION_F1_WITH_RECALL_FLOOR'}


def bucket_metrics(cases,names):
    result={}
    for bucket in ('small_1_24','medium_25_48','crowded_49_128'):
        group=[r for r in cases if r['density_bucket']==bucket]
        result[bucket]={name:base.metrics([{**r['configurations'][name],
                                           'family':r['family']} for r in group])
                        for name in names} if group else {'cases':0}
    return result


def _quantiles(values):
    if not values:return {'count':0,'median':None,'p90':None}
    values=sorted(values)
    return {'count':len(values),'median':values[(len(values)-1)//2],
            'p90':values[int(.90*(len(values)-1))]}


def audit_summary(audit):
    return {bucket:{status:{'confidence':_quantiles([v[0] for v in rows]),
                            'candidate_destinations':_quantiles([v[1] for v in rows]),
                            'gap_to_best':_quantiles([v[2] for v in rows])}
                    for status,rows in kinds.items()}
            for bucket,kinds in audit.items()}


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,output,device='cuda',max_length=2048,validation_limit=VALIDATION_LIMIT):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0005_IMMUTABLE_OUTPUT')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,prior.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(row['prompt']) for row in train))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0005_TRAIN_REPLAY')
    first=base.novel_dev_rows(train,limit=prior.CALIBRATION_LIMIT,seed=base.SEED)
    if sha_rows(first)!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0005_BOOTSTRAP_DEV_REPLAY')
    second=sel.new_validation_rows(train,first,limit=sel.VALIDATION_LIMIT)
    third=disc.new_validation_rows(train,first,second,limit=disc.VALIDATION_LIMIT)
    fourth=prior.new_validation_rows(train,first,second,third,limit=prior.VALIDATION_LIMIT)
    allowed=sel.compatibility_from_train(train)
    selection_path=Path(selection_run)
    selection=json.loads((selection_path/sel.REPORT).read_text(encoding='utf-8'))
    if (selection.get('schema')!=sel.SCHEMA or selection.get('g2_parent_hashes')!=hashes or
            selection.get('validation_dataset_sha256')!=sha_rows(second) or
            selection.get('compatibility_sha256')!=sel._hash_json([list(x) for x in sorted(allowed)]) or
            selection.get('production_promotion') is not False):
        raise ValueError('GAMER_G2_0005_G2_0002_DRIFT')
    third_hashes={**hashes,'g2_0002_report_sha256':backbone.sha256(selection_path/sel.REPORT),
                  'g2_0002_cases_sha256':backbone.sha256(selection_path/sel.CASES)}
    _,discrimination_hashes=prior.verify_discrimination_parent(discrimination_run,third,third_hashes)
    bootstrap_head=deepcopy(head)
    parent,parent_cases,parent_hashes=verify_parent(relational_run,fourth,head,
                                                    {**hashes,**discrimination_hashes},allowed)
    fresh=new_validation_rows(train,first,second,third,fourth,limit=validation_limit)
    head=head.to(device).eval().requires_grad_(False)
    bootstrap_head=bootstrap_head.to(device).eval().requires_grad_(False)
    before={name:val.detach().cpu().clone() for name,val in head.state_dict().items()}
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0005_BACKBONE_UNFROZEN')
    # Parent replay used the G2-0001 head when obtaining G1 nodes/vectors.
    # G2-0004 binding weights are independent of both these operations.
    calibration=prior.capture(bootstrap_head,model,tok,fourth,device=device,max_length=max_length,group='calibration_replay')
    validate=prior.capture(bootstrap_head,model,tok,fresh,device=device,max_length=max_length,group='new_independent')
    with torch.inference_mode():
        replay_rank_parent(calibration,parent_cases,head,allowed,device=device)
        cal_metrics,cal_saved,audit=evaluate(calibration,head,allowed,device=device,
                                    group='previously_inspected_calibration',include_diagnostics=True)
        selected,selection_info=choose_rule(cal_metrics,cal_metrics['g2_0001_argmax_050'])
        # Fresh data are evaluated only after the decoder rule is fixed.
        valid_metrics,valid_saved,_=evaluate(validate,head,allowed,device=device,
                                             group='new_independent_validation')
    prior.validate_original_state(head,before)
    gates={name:sel.validation_gate(valid_metrics[name],valid_metrics['g2_0001_argmax_050'])
           for name in decoder_rules()}
    selected_gate=gates[selected] if selected else None
    shortlist=(*BASE_RULES,'g2_0001_argmax_050', 'g2_0001_compatible_050',
               *((selected,) if selected else ()))
    report={'schema':SCHEMA,'status':'G2_FROZEN_DENSITY_DISCRIMINATION_DEVELOPMENT_NOT_CERTIFIED',
            'g1_frozen':True,'g1_development_candidate':base.G1_ARM,
            'g2_0004_rank_weights_frozen':True,'trainable_parameters':0,
            'g2_certified':False,'g2_production_unlocked':False,
            'certification_authorized':False,'production_promotion':False,
            'graph_execution_authorized':False,'memory_writes':False,
            'sealed_test_used':False,'challenge_or_test_split_used':False,
            'gold_used_for_calibration_and_offline_scoring_only':True,
            'inference_uses_predicted_g1_only':True,
            'parent_g2_0004_screen_pass':False,'parent_g2_0004_replay_pass':True,
            'parent_hashes':{**hashes,**discrimination_hashes,**parent_hashes},
            'g1_head_sha256':manifest['head_sha256'],
            'calibration_seed':prior.SEED,'calibration_cases':len(fourth),
            'calibration_dataset_sha256':sha_rows(fourth),
            'independent_seed':SEED,'independent_cases':len(fresh),
            'independent_dataset_sha256':sha_rows(fresh),
            'fixed_decoder_rules':decoder_rules(),
            'selected_rule':selected,'calibration_selection':selection_info,
            'calibration_metrics':cal_metrics,'independent_metrics':valid_metrics,
            'independent_gates_by_rule':gates,
            'selected_independent_screen_pass':bool(selected_gate and all(selected_gate.values())),
            'independent_by_node_density':bucket_metrics(valid_saved,shortlist),
            'comparison':{name:valid_metrics[name] for name in shortlist},
            'no_fixed_edge_cap':True}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(cal_saved+valid_saved))
    (output/AUDIT).write_text(json.dumps(audit_summary(audit),sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
