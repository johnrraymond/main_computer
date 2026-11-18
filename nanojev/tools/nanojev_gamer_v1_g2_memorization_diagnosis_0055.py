"""G2-0055 set-predictor memorization diagnosis, using frozen upstream evidence.

Starts from the *saved* G2-0055 set-prediction weights (not a new architecture),
continues learning exclusively on the same eight training graphs, periodically
measures exact graph errors, and saves a per-prompt, per-edge diagnostic ledger.
The original optimizer state was not saved; AdamW starts with fresh moments.
No probe graph may be scored, used for early stopping, or trained on.
"""
from __future__ import annotations

import json
from pathlib import Path

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48

SCHEMA='nanojev.gamer.graph.g2.set-memorization-diagnosis-0055.v1'
STATUS='G2_0055_SET_MEMORIZATION_DIAGNOSIS_RESEARCH_ONLY'
REPORT='g2_0055_memorization_diagnosis_report.json'
LEDGER='g2_0055_memorization_candidate_per_case.jsonl.gz'
PREDICTIONS='g2_0055_memorization_prediction_diagnostics.jsonl.gz'
WEIGHTS='g2_0055_memorization_checkpoints.safetensors'
POLICY='g2_0055_memorization_diagnosis_policy.json'
STEPS=1200
INTERVAL=80
MAX_STEPS=4000


def refuse(reason):
    raise ValueError('GAMER_G2_0055_DIAGNOSIS_'+reason)


def verify_parent55(folder, parent54_run):
    folder=Path(folder)
    record=json.loads((folder/p55.REPORT).read_text(encoding='utf-8'))
    exp=record.get('scoring_experiment',{})
    hash_file=d48.backbone.sha256
    policy=json.loads((folder/p55.POLICY).read_text(encoding='utf-8'))
    if (record.get('schema')!=p55.SCHEMA or record.get('status')!=p55.STATUS
        or record.get('production_promotion') is not False
        or record.get('sealed_test_used') is not False
        or exp.get('memorization_gate',{}).get('set_prediction',{}).get('exact')!=0
        or exp.get('memorization_gate',{}).get('set_prediction',{}).get('required')!=8
        or exp.get('gates',{}).get('any_architecture_memorized') is not False
        or exp.get('training',{}).get('memorization_max_steps_per_arm')!=240
        or record.get('scoring_weights_sha256')!=hash_file(folder/p55.WEIGHTS)
        or record.get('scoring_policy_sha256')!=hash_file(folder/p55.POLICY)
        or record.get('ledger_sha256')!=hash_file(folder/p55.LEDGER)
        or exp.get('parent54_hashes',{}).get('g2_0054_report_sha256')
            !=hash_file(Path(parent54_run)/p54.REPORT)
        or policy.get('enabled') is not False or policy.get('research_only') is not True
        or policy.get('architectures_passed_gate')!=[]):
        refuse('PARENT55_INTEGRITY_OR_GATE')
    checkpoint=load_file(str(folder/p55.WEIGHTS),device='cpu')
    expected=set(p55.model_for('set_prediction').state_dict())
    selected={key[len('set_prediction.'):]:v for key,v in checkpoint.items()
              if key.startswith('set_prediction.')}
    if set(selected)!=expected or any(not bool(torch.isfinite(v).all()) for v in selected.values()):
        refuse('PARENT55_SET_WEIGHTS_INCOMPLETE')
    model=p55.model_for('set_prediction')
    model.load_state_dict(selected,strict=True)
    return model,{'g2_0055_report_sha256':hash_file(folder/p55.REPORT),
                  'g2_0055_weights_sha256':hash_file(folder/p55.WEIGHTS),
                  'g2_0055_ledger_sha256':hash_file(folder/p55.LEDGER),
                  'g2_0055_policy_sha256':hash_file(folder/p55.POLICY)}


def _edge(sample,idx):
    src=int(sample['src'][idx]);dst=int(sample['dst'][idx]);rel=int(sample['relation'][idx])
    anchors=sample.get('anchors_debug')
    return {'edge_index':int(idx),'source_node':src,'destination_node':dst,
            'relation':g53.RELATIONS[rel],
            **({'source_span':list(anchors[src]),'destination_span':list(anchors[dst])}
               if anchors is not None else {})}


def _nearest_mismatch(sample, idx, other):
    """Post-prediction audit, not a model training target or inference rule."""
    a=(int(sample['src'][idx]),int(sample['dst'][idx]),int(sample['relation'][idx]))
    if not other:return {'nearest_edge':None,'different_fields':['source','destination','relation']}
    def key(j):
        b=(int(sample['src'][j]),int(sample['dst'][j]),int(sample['relation'][j]))
        return (sum(x!=y for x,y in zip(a,b)),int(j))
    best=min(other,key=key)
    b=(int(sample['src'][best]),int(sample['dst'][best]),int(sample['relation'][best]))
    return {'nearest_edge':_edge(sample,best),
            'different_fields':[name for name,x,y in zip(('source','destination','relation'),a,b) if x!=y]}


@torch.no_grad()
def diagnose_case(model,sample,*,step):
    """Gold is accessed only after inference; no target contaminates the decoder."""
    model.eval()
    selected=p55.infer_edges(model,sample,'set_prediction')
    gold=set(sample['positive'].tolist())
    rel,src,dst=model(sample)
    rel_prob=rel.softmax(-1)
    # Match gold triples to predicted slots for an interpretable decomposition.
    labels=sample['relation'].index_select(0,sample['positive'])+1
    sources=sample['src'].index_select(0,sample['positive'])
    targets=sample['dst'].index_select(0,sample['positive'])
    lr=rel.log_softmax(-1)
    ls=src.log_softmax(-1)
    ld=dst.log_softmax(-1)
    dev=rel.device
    cost=-(lr[:,labels.to(dev)]+ls[:,sources.to(dev)]+ld[:,targets.to(dev)]).cpu().numpy()
    slots,matches=p55.rectangular_assignment(cost)
    matched=[]
    for slot,position in zip(slots,matches):
        idx=int(sample['positive'][position]);label=int(labels[position]);source=int(sources[position]);target=int(targets[position]);slot=int(slot)
        predicted_rel=int(rel[slot].argmax());predicted_src=int(src[slot].argmax());predicted_dst=int(dst[slot].argmax())
        matched.append({'gold':_edge(sample,idx),'matched_slot':slot,
          'relationship_top1_correct':predicted_rel==label,
          'source_top1_correct':predicted_src==source,
          'destination_top1_correct':predicted_dst==target,
          'slot_null_top1':predicted_rel==0,
          'gold_relation_probability':round(float(rel_prob[slot,label]),7),
          'null_probability':round(float(rel_prob[slot,0]),7),
          'gold_relation_log_margin_vs_null':round(float(rel[slot,label]-rel[slot,0]),6),
          'predicted_relation':('null' if predicted_rel==0 else g53.RELATIONS[predicted_rel-1]),
          'predicted_source_node':predicted_src,'predicted_destination_node':predicted_dst})
    false_neg=sorted(gold-selected);false_pos=sorted(selected-gold)
    out={'section':'memorization','step':step,'prompt_sha256':sample['prompt_sha256'],
      'pair':sample['pair'],'family':sample['family'],'mode':sample['mode'],
      'gold_count':len(gold),'candidate_edges':int(sample['edge_count']),
      'predicted_count':len(selected),'count_error':len(selected)-len(gold),
      'true_positive':len(selected&gold),'false_negative':len(false_neg),
      'false_positive':len(false_pos),'exact':selected==gold and len(gold)==sample['truth_count'],
      'active_non_null_slots':int((rel.argmax(-1)!=0).sum()),
      'unique_predicted_edges':len(selected),'gold_matched_slot_analysis':matched,
      'missing_edges':[{**_edge(sample,i),'closest_prediction':_nearest_mismatch(sample,i,sorted(selected))} for i in false_neg],
      'extra_edges':[{**_edge(sample,i),'closest_gold':_nearest_mismatch(sample,i,sorted(gold))} for i in false_pos],
      'predicted_edges':[_edge(sample,i) for i in sorted(selected)]}
    return out


def checkpoint_metrics(model,small,step,*,detailed):
    diagnostics=[diagnose_case(model,x,step=step) for x in small]
    summary={'step':step,'exact':sum(x['exact'] for x in diagnostics),
       'false_negative':sum(x['false_negative'] for x in diagnostics),
       'false_positive':sum(x['false_positive'] for x in diagnostics),
       'mean_abs_count_error':round(sum(abs(x['count_error']) for x in diagnostics)/len(diagnostics),4),
       'relation_top1_correct':sum(m['relationship_top1_correct'] for x in diagnostics for m in x['gold_matched_slot_analysis']),
       'source_top1_correct':sum(m['source_top1_correct'] for x in diagnostics for m in x['gold_matched_slot_analysis']),
       'destination_top1_correct':sum(m['destination_top1_correct'] for x in diagnostics for m in x['gold_matched_slot_analysis']),
       'slot_null_top1_for_gold':sum(m['slot_null_top1'] for x in diagnostics for m in x['gold_matched_slot_analysis']),
       'gold_edges':sum(x['gold_count'] for x in diagnostics)}
    if not detailed:
        # Preserve per-case numerical diagnostics at every checkpoint; reserve
        # full edge-level snapshots for baseline, final and best checkpoints.
        diagnostics=[{k:v for k,v in row.items() if k not in (
            'gold_matched_slot_analysis','missing_edges','extra_edges','predicted_edges')}
            for row in diagnostics]
    return summary,diagnostics


def continue_memorization(model,small,*,device,steps=STEPS,interval=INTERVAL,batch=2):
    if device not in ('cpu','cuda') or not 1<=steps<=MAX_STEPS or not 1<=interval<=steps or not 1<=batch<=8:
        refuse('CONTINUATION_BUDGET')
    if len(small)!=8 or any(s['truth_count']!=len(s['positive']) for s in small):
        refuse('MEMORIZATION_COHORT_CHANGED')
    model=model.to(device).eval()
    baseline,baseline_cases=checkpoint_metrics(model,small,0,detailed=True)
    checkpoints=[baseline];rows=list(baseline_cases);snapshots={}
    best_quality=(baseline['exact'],-baseline['false_negative']-baseline['false_positive'],
                  -baseline['false_positive'])
    best_step=0
    best_state={key:value.detach().cpu().contiguous().clone() for key,value in model.state_dict().items()}
    # The original optimizer state was not saved in G2-0055. This is a new
    # AdamW optimizer on exactly the saved weights, never a claim of full resume.
    opt=torch.optim.AdamW(model.parameters(),lr=p55.LR)
    generator=torch.Generator().manual_seed(p55.SEED+1)
    history=[];actual=0
    for step in range(1,steps+1):
        model.train()
        pick=torch.randperm(8,generator=generator)[:batch].tolist()
        opt.zero_grad(set_to_none=True)
        loss=torch.stack([p55.set_objective(model,small[i]) for i in pick]).mean()
        if not bool(torch.isfinite(loss)):refuse('NONFINITE_LOSS')
        loss.backward()
        norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.)
        if not bool(torch.isfinite(norm)):refuse('NONFINITE_GRADIENT')
        opt.step();actual=step
        if step%interval==0 or step==steps:
            model.eval()
            summary,per_case=checkpoint_metrics(model,small,step,detailed=True)
            summary['loss_last_batch']=round(float(loss.detach().cpu()),6)
            checkpoints.append(summary);rows.extend(per_case)
            history.append({'step':step,'loss':summary['loss_last_batch']})
            quality=(summary['exact'],-summary['false_negative']-summary['false_positive'],
                     -summary['false_positive'])
            if quality>best_quality:
                best_quality=quality;best_step=step
                best_state={k:v.detach().cpu().contiguous().clone() for k,v in model.state_dict().items()}
            print('GAMER_G2_0055_MEMORIZATION_DIAGNOSIS '+json.dumps(summary,sort_keys=True),flush=True)
            if summary['exact']==8:break
    # Best and final parameter snapshots permit reproducible continued work.
    for name,tensor in best_state.items():snapshots['best.'+name]=tensor
    for name,tensor in model.state_dict().items():snapshots['last.'+name]=tensor.detach().cpu().contiguous().clone()
    last=checkpoints[-1]
    if last['exact']==8: outcome='EXACT_MEMORIZATION_ACHIEVED'
    elif last['false_negative']+last['false_positive']<baseline['false_negative']+baseline['false_positive']:
        outcome='IMPROVING_NOT_YET_EXACT'
    else:outcome='NO_NET_EDGE_ERROR_IMPROVEMENT'
    return {'outcome':outcome,'baseline':baseline,'checkpoints':checkpoints,
        'best_step':best_step,'best_checkpoint_quality':list(best_quality),
        'last_step':actual,'optimizer_state_resumed':False,
        'optimizer_steps_total':actual,'training_history':history},rows,snapshots


def select_same_eight_training_graphs(samples,pairs):
    """Reproduce 0055's eight-graph gate WITHOUT reading probe/fit gold labels."""
    source=pairs['counterfactual_train']
    if len(source)!=48 or len(pairs['counterfactual_probe'])!=16 or \
       len(pairs.get('fresh_counterfactual_probe',[]))!=16:
        refuse('COHORT_COUNTS_CHANGED')
    by_cell={}
    for pair in source:
        by_cell.setdefault((pair['family'],pair['mode']),[]).append(pair)
    required={(family,mode) for family in ('allocation','encounter')
                                 for mode in ('role_swap','renaming')}
    if set(by_cell)!=required or any(len(v)!=12 for v in by_cell.values()):
        refuse('MEMORIZATION_CELL_DRIFT')
    # 0054/0055 reserve the first two pairs per cell for train_fit;
    # 0055's memorization_subset chooses the first remaining pair per cell.
    chosen=[by_cell[cell][2] for cell in sorted(required)]
    expected={sha for pair in chosen for sha in pair['sha256']}
    subset=[sample for sample in samples if sample['prompt_sha256'] in expected]
    if len(expected)!=8 or len(subset)!=8 or \
       len({s['prompt_sha256'] for s in subset})!=8 or \
       any(s['section']!='counterfactual_train' for s in subset):
        refuse('MEMORIZATION_PROMPT_ID_DRIFT')
    for sample in subset:
        g53.mark_truth(sample)
    if any(sample['truth_count']!=len(sample['positive']) for sample in subset):
        refuse('MEMORIZATION_NOT_REACHABLE')
    return subset,chosen


def finalize(samples,pairs,replay,report,*,device,steps,interval,batch,parent55_model,parent55_hashes,parent48_sha):
    if replay.get('exact_parent50_legacy_replay') is not True or \
       report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True:
        refuse('PARENT52_REPLAY')
    small,small_pairs=select_same_eight_training_graphs(samples,pairs)
    # Never reference validation/probe samples below this point.
    result,rows,weights=continue_memorization(parent55_model,small,device=device,
        steps=steps,interval=interval,batch=batch)
    if len({x['prompt_sha256'] for x in rows})!=8 or any(x['section']!='memorization' for x in rows):
        refuse('DIAGNOSTIC_COHORT_LEAKAGE')
    experiment={'schema':SCHEMA,'status':STATUS,'production_promotion':False,
        'parent55_hashes':parent55_hashes,'frozen_encoder_parent48_sha256':parent48_sha,
        'cohort':{'graphs':8,'pairs':4,'source':'G2_0055_IDENTICAL_TRAIN_GATE',
                  'prompt_sha256':sorted(x['prompt_sha256'] for x in small),
                  'families_and_modes':sorted({x['family']+'/'+x['mode'] for x in small})},
        'diagnosis':result,'training':{'optimizer_steps_total':result['optimizer_steps_total'],
                                      'steps':result['optimizer_steps_total']},
        'gates':{'production_promotion':False,'generalization_evaluated':False,
                 'sealed_test_used':False,'exact_memorization_reached':result['outcome']=='EXACT_MEMORIZATION_ACHIEVED',
                 'next_step':'ANALYZE_PREDICTION_ERROR_LEDGER'},
        '_auxiliary_ledger':{'filename':PREDICTIONS,'rows':rows}}
    policy={'enabled':False,'research_only':True,'production_promotion':False,
            'generalization_evaluated':False,'sealed_test_used':False,
            'exact_memorization_reached':result['outcome']=='EXACT_MEMORIZATION_ACHIEVED'}
    return experiment,weights,policy


def run(*,candidate55_run,candidate54_run,candidate53_run,candidate52_run,
        candidate51_run,occurrence48_run,counterfactual50_run,output,device='cuda',
        continuation_steps=STEPS,checkpoint_interval=INTERVAL,batch_prompts=2,**kwargs):
    if kwargs.get('train_pairs',48)!=48 or kwargs.get('probe_pairs',16)!=16:
        refuse('COHORT_COUNTS_FIXED')
    if not 1<=continuation_steps<=MAX_STEPS or not 1<=checkpoint_interval<=continuation_steps:
        refuse('INVALID_BUDGET')
    g53.verify_parent52(candidate52_run)
    p54.verify_parent53(candidate53_run,{},candidate52_run)
    p55.verify_parent54(candidate54_run,candidate53_run,candidate52_run)
    parent_model,hashes=verify_parent55(candidate55_run,candidate54_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0055_DIAGNOSIS_IMMUTABLE_OUTPUT')
    p55.training_device_preflight(device)
    parent48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    weight_path=Path(occurrence48_run)/d48.WEIGHTS
    sha=d48.backbone.sha256(weight_path)
    if parent48.get('schema')!=d48.SCHEMA or parent48.get('weights_sha256')!=sha:
        refuse('PARENT48_HASH')
    raw=load_file(str(weight_path),device='cpu')
    frozen=d48.OccurrenceEvidenceScorer(raw['encoder.norm.weight'].shape[0]).eval().requires_grad_(False).to(device)
    frozen.load_state_dict(raw,strict=True)
    snapshot={k:v.detach().cpu().clone() for k,v in frozen.state_dict().items()}
    def features(raw_capture,expanded,section):
        features=g53.frozen_features(raw_capture,expanded,frozen,section)
        # Pure provenance; no target labels enter feature construction.
        features['anchors_debug']=list(expanded['anchors'])
        return features
    def finish(cases,pairs,replay,report):
        result=finalize(cases,pairs,replay,report,device=device,steps=continuation_steps,
            interval=checkpoint_interval,batch=batch_prompts,parent55_model=parent_model,
            parent55_hashes=hashes,parent48_sha=sha)
        if any(not torch.equal(v,frozen.state_dict()[k].detach().cpu()) for k,v in snapshot.items()):
            refuse('FROZEN_0048_MUTATION')
        return result
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,**kwargs)
