"""G2-0002: frozen G2-0001 binding-score selection on predicted G1 objects.

No parameter training, oracle-conditioned inference, test access, promotion,
execution, or memory writes. Calibrate on G2-0001's existing development
prompts; evaluate the *fixed* selected decoder on a new prompt-disjoint cohort.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import math

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, decode_graph
from tools.nanojev_gamer_v1_graph_contract import validate_graph, MAX_EDGES

SCHEMA='nanojev.gamer.graph.g2.frozen-binding-selection.v1'
REPORT='g2_binding_selection_report.json'
CASES='g2_binding_selection_per_case.jsonl'
TRAIN_LIMIT=base.TRAIN_LIMIT
CALIBRATION_LIMIT=base.DEV_LIMIT
VALIDATION_LIMIT=320
SEED=320002
THRESHOLDS=(0.50,0.65,0.80,0.90,0.97)
CAPS=(1,2,3)
RECALL_DROP_LIMIT=0.15
MIN_RECALL=0.55


def _hash_json(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def compatibility_from_train(rows):
    """Only author-marked TRAIN edges can define structural compatibility."""
    if not rows or any(r.get('split')!='train' for r in rows):
        raise ValueError('GAMER_G2_0002_COMPATIBILITY_REQUIRES_TRAIN')
    kinds=set()
    for row in rows:
        nodes={n['id']:n for n in row['graph']['nodes']}
        for edge in row['graph']['edges']:
            if edge['type'] in BIND_CLASSES[1:]:
                kinds.add((edge['type'],nodes[edge['src']]['kind'],nodes[edge['dst']]['kind']))
    if not kinds:raise ValueError('GAMER_G2_0002_EMPTY_COMPATIBILITY')
    return frozenset(kinds)


def configurations():
    controls={
        'argmax_050':{'threshold':0.50,'compatible':False,'per_source_cap':None},
        'argmax_080':{'threshold':0.80,'compatible':False,'per_source_cap':None},
        'compatible_050':{'threshold':0.50,'compatible':True,'per_source_cap':None},
        'top2_050':{'threshold':0.50,'compatible':False,'per_source_cap':2},
    }
    candidates={f'compatible_cap{cap}_p{int(round(p*100)):02d}':
                {'threshold':p,'compatible':True,'per_source_cap':cap}
                for cap in CAPS for p in THRESHOLDS}
    return controls,candidates


def choose_edges(anchors,logits,rule,allowed):
    """Deterministic source/type-limited directed decoder without gold input.

    Inputs are only G1 predicted anchors, frozen G2 scores, and the train-only
    compatibility inventory. No lexical/semantic rules or family routing.
    """
    if len(anchors)>128 or logits.shape!=(len(anchors),len(anchors),len(BIND_CLASSES)):
        raise ValueError('GAMER_G2_0002_PAIR_SHAPE')
    if not torch.isfinite(logits).all():raise ValueError('GAMER_G2_0002_NONFINITE_SCORES')
    p=rule['threshold'];cap=rule['per_source_cap']
    if type(p) not in (int,float) or not 0<p<=1 or (cap is not None and (type(cap) is not int or cap<1)):
        raise ValueError('GAMER_G2_0002_BAD_RULE')
    probs=logits.detach().float().cpu().softmax(-1)
    options=[]
    for i,a in enumerate(anchors):
        for j,b in enumerate(anchors):
            if i==j:continue
            k=int(probs[i,j].argmax())
            if k==0:continue
            confidence=float(probs[i,j,k])
            if confidence<p:continue
            typ=BIND_CLASSES[k]
            if rule['compatible'] and (typ,a[2],b[2]) not in allowed:continue
            options.append((-confidence,i,j,k))
    options.sort()  # high-confidence first; stable index tie-breaker
    selected=set();per_source=Counter()
    for _,i,j,k in options:
        typ=BIND_CLASSES[k]
        key=(i,typ)
        if cap is not None and per_source[key]>=cap:continue
        candidate=(anchors[i],anchors[j],typ)
        if candidate in selected:continue
        selected.add(candidate);per_source[key]+=1
        if len(selected)>MAX_EDGES:raise ValueError('GAMER_G2_0002_EDGE_LIMIT')
    return selected


def score_set(predicted,gold):
    return {'correct':len(predicted&gold),'missing':len(gold-predicted),
            'false_positive':len(predicted-gold),'exact':predicted==gold}


def _case_scores(item,rules,allowed):
    # Inference is a pure function of (anchors, frozen logits, train rules).
    return {name:score_set(choose_edges(item['anchors'],item['logits'],rule,allowed),item['gold'])
            for name,rule in rules.items()}


def _metric(rows):
    return base.metrics(rows)


def _summarize(cases,rule_set,allowed):
    outcome={name:[] for name in rule_set}
    for case in cases:
        values=_case_scores(case,rule_set,allowed)
        for name,result in values.items():
            outcome[name].append({**result,'family':case['family']})
    return {name:_metric(values) for name,values in outcome.items()}


def select_calibrated_rule(results,controls,candidates):
    """Choose using calibration only, with a declared recall-retention floor."""
    reference=results['argmax_050']
    floor=max(MIN_RECALL,reference['recall']-RECALL_DROP_LIMIT)
    eligible=[name for name in candidates if results[name]['recall']>=floor]
    if not eligible:
        return None,{'calibration_recall_floor':floor,'eligible_candidates':0,
                     'reason':'NO_CANDIDATE_SATISFIES_RECALL_FLOOR'}
    best=max(eligible,key=lambda name:(results[name]['f1'],results[name]['precision'],
                                       -results[name]['false_positive'],name))
    return best,{'calibration_recall_floor':floor,'eligible_candidates':len(eligible),
                 'reason':'MAX_CALIBRATION_F1_SUBJECT_TO_RECALL_FLOOR'}


def new_validation_rows(train,calibration,*,limit=VALIDATION_LIMIT,seed=SEED):
    if not train or not calibration or limit<2 or limit%2:
        raise ValueError('GAMER_G2_0002_INVALID_VALIDATION_SIZE')
    rows=base.novel_dev_rows(train+calibration,limit=limit,seed=seed)
    excluded={base._sha(r['prompt']) for r in train+calibration}
    hashes=[base._sha(r['prompt']) for r in rows]
    if set(hashes)&excluded or len(set(hashes))!=len(hashes):
        raise ValueError('GAMER_G2_0002_PROMPT_LEAKAGE')
    if Counter(r['family'] for r in rows)!={'allocation':limit//2,'encounter':limit//2}:
        raise ValueError('GAMER_G2_0002_UNBALANCED_VALIDATION')
    return rows


def load_parents(root,g1_report,g2_run):
    """Fail closed on lineage, incomplete output, or noncanonical weights."""
    root=Path(root);g2_run=Path(g2_run)
    head,manifest,dataset,validation_sha=base.validate_parent(root,g1_report)
    report_path=g2_run/base.REPORT
    cases_path=g2_run/base.CASES
    weights_path=g2_run/base.HEAD
    report=json.loads(report_path.read_text(encoding='utf-8'))
    required={'schema':base.SCHEMA,'g2_development_started':True,
              'g2_certified':False,'g2_production_unlocked':False,
              'g1_frozen':True,'g1_development_candidate':base.G1_ARM,
              'g1_known_residual_losses':1,'g2_inference_uses_predicted_g1_only':True,
              'g2_training_only_author_gold':True,
              'certification_authorized':False,'challenge_or_test_split_used':False,
              'sealed_test_used':False,'production_promotion':False,
              'graph_execution_authorized':False,'memory_writes':False,
              'train_cases':TRAIN_LIMIT,'dev_cases':CALIBRATION_LIMIT,
              'train_steps':base.STEPS}
    for name,value in required.items():
        if type(report.get(name)) is not type(value) or report[name]!=value:
            raise ValueError('GAMER_G2_0002_PARENT_CONTRACT:'+name)
    checks={'g1_independent_validation_report_sha256':validation_sha,
            'g1_head_sha256':manifest['head_sha256'],
            'g1_dataset_sha256':manifest['dataset_sha256'],
            'g1_source_adapter_sha256':manifest['source_adapter_sha256'],
            'source_adapter_sha256':backbone.adapter_fingerprint(manifest['source_adapter'])}
    for name,value in checks.items():
        if report.get(name)!=value:raise ValueError('GAMER_G2_0002_PARENT_LINEAGE:'+name)
    if backbone.sha256(weights_path)!=report.get('g2_development_weights_sha256'):
        raise ValueError('GAMER_G2_0002_WEIGHTS_HASH_MISMATCH')
    weights=load_file(str(weights_path),device='cpu')
    expected=base._binding_snapshot(head)
    if set(weights)!=set(expected):raise ValueError('GAMER_G2_0002_WEIGHTS_KEYS_CHANGED')
    for key,value in weights.items():
        if value.shape!=expected[key].shape or not torch.isfinite(value).all():
            raise ValueError('GAMER_G2_0002_WEIGHTS_INVALID:'+key)
    old={k:v.detach().clone() for k,v in head.state_dict().items()
         if k not in weights}
    head.load_state_dict(weights,strict=False)
    base._assert_g1_frozen(head,old)
    saved_cases=[json.loads(s) for s in cases_path.read_text(encoding='utf-8').splitlines() if s.strip()]
    if len(saved_cases)!=CALIBRATION_LIMIT or any(set(x.get('configurations',{}))!={'untrained','trained','zero_node_features'} for x in saved_cases):
        raise ValueError('GAMER_G2_0002_PARENT_CASES_MISMATCH')
    return head,manifest,dataset,report,saved_cases,old,{
        'g1_validation_report_sha256':validation_sha,
        'g2_bootstrap_report_sha256':backbone.sha256(report_path),
        'g2_bootstrap_cases_sha256':backbone.sha256(cases_path),
        'g2_bootstrap_weights_sha256':backbone.sha256(weights_path)}


def capture(head,manifest,model,tok,rows,*,device,max_length,group):
    out=[]
    with torch.inference_mode():
        for i,row in enumerate(rows,1):
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device)
            g1=head(hidden,eligible=prepared['eligible'].to(device),active_stage='G1')
            spans=base.predicted_spans(g1,prepared)
            g2=head(hidden,eligible=prepared['eligible'].to(device),training_spans=spans,active_stage='G2')
            anchors=base.predicted_anchors(spans,prepared)
            n=len(anchors)
            if n!=len(spans):raise ValueError('GAMER_G2_0002_UNMAPPABLE_NODES')
            logits=g2['binding_logits'][:n,:n].detach().cpu().contiguous()
            # Replay G2-0001's actual argmax decoder and verify the graph IR.
            legacy=base.score_output(g2,prepared,row)
            graph,_=decode_graph(g2,prepared,unlocked='G2')
            validate_graph(graph,complete=False)
            out.append({'index':i,'family':row['family'],'anchors':anchors,
                        'logits':logits,'gold':base.gold_binding_edges(row),
                        'prompt_sha256':base._sha(row['prompt']),
                        'legacy':legacy})
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_BINDING_SELECTION_CAPTURE {group} {i}/{len(rows)}',flush=True)
    return out


def verify_bootstrap_replay(rows,saved,report,allowed):
    if len(rows)!=len(saved):raise ValueError('GAMER_G2_0002_REPLAY_LENGTH_CHANGED')
    for row,old in zip(rows,saved):
        if (old.get('index')!=row['index'] or old.get('family')!=row['family'] or
                old.get('prompt_sha256')!=row['prompt_sha256']):
            raise ValueError('GAMER_G2_0002_REPLAY_IDENTITY_CHANGED')
        scored=score_set(choose_edges(row['anchors'],row['logits'],configurations()[0]['argmax_050'],allowed),row['gold'])
        if scored!=row['legacy'] or scored!=old['configurations']['trained']:
            raise ValueError('GAMER_G2_0002_REPLAY_PREDICTIONS_CHANGED')
    baseline=_metric([{**row['legacy'],'family':row['family']} for row in rows])
    if baseline!=report.get('metrics',{}).get('trained'):
        raise ValueError('GAMER_G2_0002_REPLAY_METRICS_CHANGED')


def validation_gate(selected,base_result):
    """Development screening only; a failing rule remains reported, not promoted."""
    return {'precision_at_least_double_baseline':selected['precision']>=2*base_result['precision'],
            'recall_at_least_060':selected['recall']>=0.60,
            'false_positives_at_most_40_percent':selected['false_positive']<=0.4*base_result['false_positive'],
            'f1_improves':selected['f1']>base_result['f1'],
            'no_gold_labels_as_inference_inputs':True}


def run(*,root,output,g1_validation_report,g2_run,device='cuda',max_length=2048,
        validation_limit=VALIDATION_LIMIT):
    root=Path(root);output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0002_IMMUTABLE_OUTPUT')
    head,manifest,dataset,parent,cases,upstream,hashes=load_parents(root,g1_validation_report,g2_run)
    train=base.original_training_rows(dataset,TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in train))!=parent.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0002_TRAIN_REPLAY_CHANGED')
    calibration=base.novel_dev_rows(train,limit=CALIBRATION_LIMIT,seed=base.SEED)
    if hashlib.sha256(base._jsonl(calibration)).hexdigest()!=parent.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0002_CALIBRATION_REPLAY_CHANGED')
    validation=new_validation_rows(train,calibration,limit=validation_limit)
    allowed=compatibility_from_train(train)
    controls,candidates=configurations();rules={**controls,**candidates}
    head=head.to(device).eval().requires_grad_(False)
    if any(p.requires_grad for p in head.parameters()):
        raise ValueError('GAMER_G2_0002_HEAD_NOT_FROZEN')
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):raise ValueError('GAMER_G2_0002_BACKBONE_UNFROZEN')
    old_state={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    prior=capture(head,manifest,model,tok,calibration,device=device,max_length=max_length,group='calibration')
    verify_bootstrap_replay(prior,cases,parent,allowed)
    calibration_results=_summarize(prior,rules,allowed)
    best,selection=select_calibrated_rule(calibration_results,controls,candidates)
    # The following independent data are never used to choose a rule.
    fresh=capture(head,manifest,model,tok,validation,device=device,max_length=max_length,group='independent')
    evaluation_rules={**controls,**({'selected_'+best:candidates[best]} if best else {})}
    evaluation=_summarize(fresh,evaluation_rules,allowed)
    gate=validation_gate(evaluation['selected_'+best],evaluation['argmax_050']) if best else None
    for name,old in old_state.items():
        if not torch.equal(old,head.state_dict()[name].detach().cpu()):
            raise ValueError('GAMER_G2_0002_FROZEN_PARAMETER_CHANGED:'+name)
    # Write bounded, explicit case comparisons without saving gold or scores
    # as a deployment asset. The JSONL is only a development audit artifact.
    per_case=[]
    for group,rows,rule_set in [('calibration',prior,rules),('independent_validation',fresh,evaluation_rules)]:
        for row in rows:
            per_case.append({'group':group,'index':row['index'],'family':row['family'],
                             'prompt_sha256':row['prompt_sha256'],
                             'g1_predicted_nodes':len(row['anchors']),'gold_binding_edges':len(row['gold']),
                             'configurations':_case_scores(row,rule_set,allowed)})
    report={'schema':SCHEMA,'status':'G2_FROZEN_BINDING_SELECTION_DEVELOPMENT_ONLY',
            'g1_development_candidate':base.G1_ARM,'g1_frozen':True,
            'g2_development_weights_frozen':True,'trainable_parameters':0,
            'g2_certified':False,'g2_production_unlocked':False,'production_promotion':False,
            'certification_authorized':False,'graph_execution_authorized':False,
            'memory_writes':False,'sealed_test_used':False,'challenge_or_test_split_used':False,
            'oracle_input_to_decoder':False,'compatibility_source':'author_marked_train_only',
            'compatibility_rules':[list(x) for x in sorted(allowed)],
            'compatibility_sha256':_hash_json([list(x) for x in sorted(allowed)]),
            'g2_parent_hashes':hashes,
            'parent_bootstrap_dev_cases':len(calibration),
            'parent_bootstrap_replay_pass':True,
            'validation_cases':len(validation),
            'validation_dataset_sha256':hashlib.sha256(base._jsonl(validation)).hexdigest(),
            'validation_seed':SEED,'calibration_only_rule_selection':True,
            'decoder_rules_predeclared':rules,
            'calibration_metrics':calibration_results,
            'selected_rule':best,'selection':selection,
            'independent_validation_metrics':evaluation,
            'independent_screen_checks':gate,
            'independent_development_screen_pass':bool(gate and all(gate.values())),
            'g1_parent_head_sha256':manifest['head_sha256']}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/CASES).write_bytes(base._jsonl(per_case))
    return report
