"""G2-0055 exact step-320 set predictor: zero-training generalization evaluation.

Evaluate ONLY the 16 reserved train-fit, 32 existing counterfactual probe and
32 fresh synthetic probe graphs. No optimizer, model updates, calibration,
checkpoint selection, or use of gold labels during prediction. Frozen G1 and
G2-0048 evidence is recaptured through the existing G2-0052 interface.
"""
from __future__ import annotations

import json
from pathlib import Path

import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_memorization_diagnosis_0055 as mem
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48

SCHEMA='nanojev.gamer.graph.g2.frozen-generalization-0055.v1'
STATUS='G2_0055_FROZEN_GENERALIZATION_EVALUATED_NOT_CERTIFIED'
REPORT='g2_0055_frozen_generalization_report.json'
LEDGER='g2_0055_frozen_generalization_candidate_per_case.jsonl.gz'
PREDICTIONS='g2_0055_frozen_generalization_predictions.jsonl.gz'
WEIGHTS='g2_0055_frozen_generalization_snapshot.safetensors'
POLICY='g2_0055_frozen_generalization_policy.json'
SECTIONS=('train_fit','counterfactual_probe','fresh_counterfactual_probe')
COUNTS={'train_fit':16,'counterfactual_probe':32,'fresh_counterfactual_probe':32}


def refuse(why):
    raise ValueError('GAMER_G2_0055_FROZEN_EVALUATION_'+why)


def verify_frozen_checkpoint(folder, parent55, parent54):
    """Read immutable step-320 BEST checkpoint, verifying all actual saved parents."""
    folder=Path(folder)
    report=json.loads((folder/mem.REPORT).read_text(encoding='utf-8'))
    exp=report.get('scoring_experiment',{})
    diagnosis=exp.get('diagnosis',{})
    policy=json.loads((folder/mem.POLICY).read_text(encoding='utf-8'))
    checksum=d48.backbone.sha256
    parent_model,parent55_hashes=mem.verify_parent55(parent55,parent54)
    del parent_model
    if (report.get('schema')!=mem.SCHEMA
        or report.get('status')!=mem.STATUS
        or report.get('production_promotion') is not False
        or report.get('sealed_test_used') is not False
        or report.get('new_optimizer_steps')!=320
        or report.get('scorer_retraining_performed') is not True
        or diagnosis.get('outcome')!='EXACT_MEMORIZATION_ACHIEVED'
        or diagnosis.get('best_step')!=320
        or diagnosis.get('last_step')!=320
        or diagnosis.get('optimizer_steps_total')!=320
        or diagnosis.get('checkpoints',[])[-1].get('exact')!=8
        or diagnosis['checkpoints'][-1].get('false_negative')!=0
        or diagnosis['checkpoints'][-1].get('false_positive')!=0
        or exp.get('gates',{}).get('exact_memorization_reached') is not True
        or exp.get('gates',{}).get('generalization_evaluated') is not False
        or exp.get('parent55_hashes')!=parent55_hashes
        or report.get('scoring_weights_sha256')!=checksum(folder/mem.WEIGHTS)
        or report.get('scoring_policy_sha256')!=checksum(folder/mem.POLICY)
        or report.get('ledger_sha256')!=checksum(folder/mem.LEDGER)
        or exp.get('auxiliary_prediction_ledger',{}).get('sha256')!=checksum(folder/mem.PREDICTIONS)
        or exp.get('auxiliary_prediction_ledger',{}).get('rows')!=40
        or len(exp.get('cohort',{}).get('prompt_sha256',[]))!=8
        or policy.get('enabled') is not False
        or policy.get('exact_memorization_reached') is not True
        or policy.get('generalization_evaluated') is not False
        or policy.get('production_promotion') is not False):
        refuse('PARENT_DIAGNOSIS_OR_HASH')
    data=load_file(str(folder/mem.WEIGHTS),device='cpu')
    expected=set(p55.model_for('set_prediction').state_dict())
    if set(data)!={prefix+name for prefix in ('best.','last.') for name in expected}:
        refuse('CHECKPOINT_KEYS')
    if any(not bool(torch.isfinite(v).all()) for v in data.values()):
        refuse('NONFINITE_CHECKPOINT')
    if any(not torch.equal(data['best.'+k],data['last.'+k]) for k in expected):
        refuse('BEST_LAST_STEP_320_DRIFT')
    model=p55.model_for('set_prediction')
    model.load_state_dict({k:data['best.'+k] for k in expected},strict=True)
    model.eval().requires_grad_(False)
    receipt={'g2_0055_diagnosis_report_sha256':checksum(folder/mem.REPORT),
             'g2_0055_diagnosis_weights_sha256':checksum(folder/mem.WEIGHTS),
             'g2_0055_diagnosis_prediction_ledger_sha256':checksum(folder/mem.PREDICTIONS),
             'g2_0055_frozen_source_step':320}
    return model,receipt,set(exp['cohort']['prompt_sha256'])


def device_preflight(model,device):
    """Real forward inference, with NO optimizer or gold tensors."""
    if device not in ('cpu','cuda'):refuse('DEVICE')
    model.to(device).eval().requires_grad_(False)
    sample={'node':torch.randn(5,96,dtype=torch.float16),
            'kinds':torch.zeros(5,dtype=torch.long), 'margin':torch.zeros(5),
            'src':torch.tensor([0,1,2],dtype=torch.long),
            'dst':torch.tensor([1,2,3],dtype=torch.long),
            'relation':torch.tensor([0,1,0],dtype=torch.long),
            'edge_count':3}
    with torch.inference_mode():
        got=p55.infer_edges(model,sample,'set_prediction')
    if not isinstance(got,set) or any(not 0<=i<3 for i in got):
        refuse('DEVICE_PREFLIGHT')
    if any(p.grad is not None for p in model.parameters()):refuse('UNEXPECTED_GRADIENT')
    print('GAMER_G2_0055_FROZEN_DEVICE_PREFLIGHT '+json.dumps(
        {'device':device,'status':'PASS','optimizer_steps':0,'mode':'inference_only'},sort_keys=True),flush=True)


def eval_cohorts(model,samples,pairs,gate_ids):
    """No training-set evaluation. Gold labels are accessed only after prediction."""
    cohorts,pair_sets=p54.split_cohorts(samples,pairs)
    selected_gate,_=p55.memorization_subset(cohorts,pair_sets)
    exact_gate={x['prompt_sha256'] for x in selected_gate}
    if len(gate_ids)!=8 or gate_ids!=exact_gate:
        refuse('MEMORIZATION_GATE_COHORT_DRIFT')
    output={};prediction_rows=[];used=set()
    for section in SECTIONS:
        selected=cohorts[section];pair_list=pair_sets[section]
        if len(selected)!=COUNTS[section] or len(pair_list)!=COUNTS[section]//2:
            refuse('EVALUATION_COHORT_SIZE')
        ids={x['prompt_sha256'] for x in selected}
        if len(ids)!=len(selected) or ids&used or ids&gate_ids:
            refuse('COHORT_OVERLAP')
        used.update(ids)
        pair_by_sha={sha:p['id'] for p in pair_list for sha in p['sha256']}
        if set(pair_by_sha)!=ids:refuse('PAIR_MAPPING')
        stats=[]
        for sample in selected:
            # Remove every key bearing gold-label information before inference.
            inference={k:v for k,v in sample.items() if k not in
                       ('positive','truth_count','labels','edges')}
            with torch.inference_mode():
                predicted=p55.infer_edges(model,inference,'set_prediction')
            if not isinstance(predicted,set) or any(not 0<=i<sample['edge_count'] for i in predicted):
                refuse('INFERENCE_EDGE_ID')
            gold=set(sample['positive'].tolist())
            if len(gold)>sample['truth_count']:refuse('GOLD_COUNTS')
            missing=sorted(gold-predicted)
            extra=sorted(predicted-gold)
            row={'section':section,'prompt_sha256':sample['prompt_sha256'],
                 'pair':sample['pair'],'family':sample['family'],'mode':sample['mode'],
                 'candidate_edges':sample['edge_count'],
                 'gold_count':sample['truth_count'],'gold_reachable_count':len(gold),
                 'predicted_count':len(predicted),'true_positive':len(predicted&gold),
                 'missing_count':sample['truth_count']-len(predicted&gold),
                 'extra_count':len(extra),
                 'candidate_reachable':len(gold)==sample['truth_count'],
                 'exact':predicted==gold and len(gold)==sample['truth_count'],
                 'predicted_edge_indices':sorted(predicted),
                 'false_negative_edge_indices':missing,
                 'false_positive_edge_indices':extra}
            stats.append(row);prediction_rows.append(row)
        by_sha={x['prompt_sha256']:x for x in stats}
        result={'cases':len(stats),'candidate_reachable':sum(x['candidate_reachable'] for x in stats),
                'exact':sum(x['exact'] for x in stats),
                'missing':sum(x['missing_count'] for x in stats),
                'extra':sum(x['extra_count'] for x in stats),
                'exact_pairs':sum(all(by_sha[sha]['exact'] for sha in p['sha256']) for p in pair_list),
                'pairs':len(pair_list),
                'families':{family:{'cases':sum(x['family']==family for x in stats),
                    'exact':sum(x['family']==family and x['exact'] for x in stats),
                    'missing':sum(x['missing_count'] for x in stats if x['family']==family),
                    'extra':sum(x['extra_count'] for x in stats if x['family']==family)}
                    for family in ('allocation','encounter')},
                'modes':{mode:{'cases':sum(x['mode']==mode for x in stats),
                    'exact':sum(x['mode']==mode and x['exact'] for x in stats)}
                    for mode in ('role_swap','renaming')}}
        output[section]=result
        print('GAMER_G2_0055_FROZEN_EVAL '+json.dumps({'section':section,**result},sort_keys=True),flush=True)
    if len(prediction_rows)!=80 or len(used)!=80:refuse('TOTAL_COHORT_SIZE')
    return output,prediction_rows


def finalize(samples,pairs,replay,report,*,model,source_hashes,gate_ids):
    if replay.get('exact_parent50_legacy_replay') is not True or \
            report.get('interface_repair',{}).get('decision',{}).get('coverage_targets_met') is not True:
        refuse('PARENT52_REPLAY')
    before={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
    results,rows=eval_cohorts(model,samples,pairs,gate_ids)
    if any(not torch.equal(v,model.state_dict()[k].detach().cpu()) for k,v in before.items()):
        refuse('FROZEN_MODEL_CHANGED')
    if any(p.grad is not None or p.requires_grad for p in model.parameters()):
        refuse('TRAINING_LEAK')
    experiment={'schema':SCHEMA,'status':STATUS,'production_promotion':False,
       'source_checkpoint':source_hashes,
       'checkpoint_selection':'G2_0055_MEMORIZATION_BEST_STEP_320_FROZEN',
       'evaluation':{'results':results,
            'cohorts_evaluated':list(SECTIONS),
            'graphs_evaluated':len(rows),
            'memorization_graphs_evaluated':0,
            'sealed_test_used':False,
            'fresh_probe_is_synthetic_not_sealed':True,
            'training_fit_previously_reserved_for_other_models_calibration':True,
            'no_gold_in_inference':True,
            'no_checkpoint_selection_or_threshold_tuning':True},
       'training':{'optimizer_steps_total':0,'optimizer_created':False},
       'gates':{'model_frozen':True,'generalization_evaluated':True,
           'production_promotion':False,'g2_certified':False,'sealed_test_used':False,
           'next_step':'EVALUATE_TRANSFER_AND_RETRAIN_ONLY_IF_JUSTIFIED'},
       '_auxiliary_ledger':{'filename':PREDICTIONS,'rows':rows}}
    weights={k:v.detach().cpu().contiguous().clone() for k,v in model.state_dict().items()}
    policy={'enabled':False,'research_only':True,'model_frozen':True,
        'optimizer_steps_total':0,'production_promotion':False,'sealed_test_used':False}
    return experiment,weights,policy


def run(*,diagnosis55_run,candidate55_run,candidate54_run,candidate53_run,
        candidate52_run,candidate51_run,occurrence48_run,counterfactual50_run,
        output,device='cuda',**kwargs):
    if kwargs.get('train_pairs',48)!=48 or kwargs.get('probe_pairs',16)!=16:
        refuse('COHORT_COUNTS_FIXED')
    g53.verify_parent52(candidate52_run)
    p54.verify_parent53(candidate53_run,{},candidate52_run)
    p55.verify_parent54(candidate54_run,candidate53_run,candidate52_run)
    parent_model,sha,gate_ids=verify_frozen_checkpoint(diagnosis55_run,candidate55_run,candidate54_run)
    if Path(output).exists():raise FileExistsError('GAMER_G2_0055_FROZEN_EVAL_IMMUTABLE_OUTPUT')
    device_preflight(parent_model,device)
    parent48=json.loads((Path(occurrence48_run)/d48.REPORT).read_text(encoding='utf-8'))
    weight_path=Path(occurrence48_run)/d48.WEIGHTS
    encoder_sha=d48.backbone.sha256(weight_path)
    if parent48.get('schema')!=d48.SCHEMA or parent48.get('weights_sha256')!=encoder_sha:
        refuse('PARENT48_HASH')
    previous=load_file(str(weight_path),device='cpu')
    encoder=d48.OccurrenceEvidenceScorer(previous['encoder.norm.weight'].shape[0])
    encoder.load_state_dict(previous,strict=True)
    encoder.eval().requires_grad_(False).to(device)
    frozen_snapshot={k:v.detach().cpu().clone() for k,v in encoder.state_dict().items()}
    def features(raw,expanded,section):
        return g53.frozen_features(raw,expanded,encoder,section)
    def finish(cases,pairs,replay,report):
        result=finalize(cases,pairs,replay,report,model=parent_model,
                        source_hashes=sha,gate_ids=gate_ids)
        if any(not torch.equal(v,encoder.state_dict()[k].detach().cpu())
               for k,v in frozen_snapshot.items()):
            refuse('FROZEN_0048_MUTATION')
        return result
    return p52.run(candidate51_run=candidate51_run,output=output,
        scoring_case_features=features,scoring_finalize=finish,
        scoring_schema=SCHEMA,scoring_status=STATUS,
        scoring_report_name=REPORT,scoring_ledger_name=LEDGER,
        interface_inference_only=True,
        alternate_weights_filename=WEIGHTS,alternate_policy_filename=POLICY,
        occurrence48_run=occurrence48_run,counterfactual50_run=counterfactual50_run,
        device=device,fresh_probe_pairs=16,**kwargs)
