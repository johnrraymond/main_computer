"""Read-only, same-prompt G1 attribution: greedy baseline / global / newly trained.

Only evaluates the development split; it cannot certify any checkpoint or
inspect the sealed test split. Uses one frozen Qwen feature capture per prompt.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import torch

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import (
    decode_greedy_spans, decode_neural_spans, token_span_char_bounds,
)


def _stats(records):
    correct=expected=predicted=exact=0
    per_kind_gold=Counter();per_kind_correct=Counter();per_kind_pred=Counter()
    for pred,gold in records:
        correct+=len(pred & gold);predicted+=len(pred);expected+=len(gold)
        exact+=int(pred==gold)
        for a,b,kind in gold:per_kind_gold[kind]+=1
        for a,b,kind in pred:per_kind_pred[kind]+=1
        for a,b,kind in pred & gold:per_kind_correct[kind]+=1
    precision=correct/max(predicted,1)
    recall=correct/max(expected,1)
    return {'cases':len(records),'exact_prompts':exact,
            'object_exact_rate':exact/max(len(records),1),
            'precision':precision,'recall':recall,
            'f1':2*precision*recall/max(precision+recall,1e-12),
            'correct':correct,'predicted':predicted,'expected':expected,
            'by_kind':{kind:{'correct':per_kind_correct[kind],
                             'total':per_kind_gold[kind],
                             'recall':per_kind_correct[kind]/per_kind_gold[kind]}
                       for kind in SPAN_KINDS if per_kind_gold[kind]}}


def _char_set(spans,prepared):
    result=set()
    for span in spans:
        bounds=token_span_char_bounds(span,prepared)
        if bounds is not None:result.add((*bounds,span['kind']))
    return result


def compare(*, baseline_dataset, baseline_checkpoint, output,
            candidate_dataset=None, candidate_checkpoint=None,
            device='cuda', max_length=2048):
    output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_GRAPH_COMPARISON_IMMUTABLE')
    if bool(candidate_dataset) != bool(candidate_checkpoint):
        raise ValueError('GAMER_GRAPH_COMPARISON_REQUIRES_BOTH_CANDIDATE_INPUTS')
    base=Path(baseline_dataset)
    candidate=Path(candidate_dataset) if candidate_dataset else None
    old_head,old_manifest=cur._load_checkpoint(baseline_checkpoint,device=device)
    new_head,new_manifest=(cur._load_checkpoint(candidate_checkpoint,device=device)
                           if candidate_checkpoint else (None,None))
    if any(m['stage']!='G1' for m in (old_manifest,new_manifest) if m is not None):
        raise PermissionError('GAMER_GRAPH_COMPARISON_G1_ONLY')
    if (old_manifest['dataset_sha256']!=backbone.sha256(base) or
            (new_manifest is not None and new_manifest['dataset_sha256']!=backbone.sha256(candidate))):
        raise ValueError('GAMER_GRAPH_COMPARISON_CHECKPOINT_DATASET_LINEAGE')
    if (new_manifest is not None and
            (old_manifest['source_adapter_sha256']!=new_manifest['source_adapter_sha256'] or
             old_manifest['model_id']!=new_manifest['model_id'])):
        raise ValueError('GAMER_GRAPH_COMPARISON_SOURCE_MISMATCH')
    if old_manifest['source_adapter_sha256']!=backbone.adapter_fingerprint(old_manifest['source_adapter']):
        raise ValueError('GAMER_GRAPH_COMPARISON_SOURCE_TAMPERED')
    before=cur.load_rows(base)
    if candidate is not None:
        after=cur.load_rows(candidate)
        # Extension must preserve every original row; only training may grow.
        if len(after)<len(before) or after[:len(before)]!=before or any(
                r['split']!='train' for r in after[len(before):]):
            raise ValueError('GAMER_GRAPH_COMPARISON_EVALUATION_ROWS_CHANGED')
    dev=[r for r in before if r['split']=='dev']
    if not dev:
        raise ValueError('GAMER_GRAPH_COMPARISON_NO_DEV')
    model,tok=backbone.model_components(old_manifest['model_id'],
                                        old_manifest['source_adapter'],device)
    old_head.eval()
    if new_head is not None:new_head.eval()
    results={'greedy_baseline':[],'global_decoder_only':[]}
    if new_head is not None:results['global_plus_refinement']=[]
    for i,row in enumerate(dev,1):
        item=cur.capture(model,tok,row,device,max_length)
        h=item['hidden'].to(device=device,dtype=torch.float32)
        prepared=item['prepared']
        with torch.inference_mode():
            old=old_head(h,eligible=prepared['eligible'].to(device),active_stage='G1')
            new=(new_head(h,eligible=prepared['eligible'].to(device),active_stage='G1')
                 if new_head is not None else None)
        gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
        choices={
            'greedy_baseline':decode_greedy_spans(old['span_logits'],old['span_valid']),
            'global_decoder_only':decode_neural_spans(old['span_logits'],old['span_valid']),
        }
        if new is not None:
            choices['global_plus_refinement']=decode_neural_spans(new['span_logits'],new['span_valid'])
        for name,spans in choices.items():
            results[name].append((_char_set(spans,prepared),gold))
        if i==1 or i%10==0 or i==len(dev):
            print(f'GAMER_GRAPH_G1_COMPARE {i}/{len(dev)}',flush=True)
    metrics={name:_stats(records) for name,records in results.items()}
    old=metrics['greedy_baseline']
    only=metrics['global_decoder_only']
    required=None
    if new_head is not None:
        if new_manifest.get('g1_refined_head_sha256')!=old_manifest['head_sha256']:
            raise ValueError('GAMER_GRAPH_COMPARISON_NOT_REFINED_FROM_BASELINE')
        refined=metrics['global_plus_refinement']
        required={
            'strict_object_sets_improve_over_original':
                refined['object_exact_rate']>old['object_exact_rate'],
            'actor_recall_improves_over_original':
                refined['by_kind'].get('actor',{}).get('recall',0) >
                old['by_kind'].get('actor',{}).get('recall',0),
            'action_recall_improves_over_original':
                refined['by_kind'].get('action',{}).get('recall',0) >
                old['by_kind'].get('action',{}).get('recall',0),
            'quantity_recall_retained':
                refined['by_kind'].get('quantity',{}).get('recall',0) >=
                old['by_kind'].get('quantity',{}).get('recall',0),
            'outcome_recall_retained':
                refined['by_kind'].get('outcome',{}).get('recall',0) >=
                old['by_kind'].get('outcome',{}).get('recall',0),
            'precision_regression_under_2pp':refined['precision']>=old['precision']-.02,
            'neural_refinement_beyond_decoder_only':
                refined['f1']>only['f1'],
        }
    report={'schema':'nanojev.gamer.graph.g1.controlled-comparison.v1',
            'status':'DEVELOPMENT_COMPARISON_ONLY_NOT_CERTIFICATION',
            'split':'dev','cases':len(dev),
            'baseline_head_sha256':old_manifest['head_sha256'],
            'candidate_head_sha256':new_manifest['head_sha256'] if new_manifest else None,
            'baseline_dataset_sha256':old_manifest['dataset_sha256'],
            'candidate_dataset_sha256':new_manifest['dataset_sha256'] if new_manifest else None,
            'blind_test_used':False,'production_promotion':False,
            'results':metrics,
            'candidate_training_controls':({
                key:new_manifest.get(key) for key in (
                    'structured_weight','consistency_weight','g1_family_balanced',
                    'g1_microbatch_family_counts','counterfactual_fraction',
                    'steps','accum','lr','seed','train_examples','g1_refined_head_sha256')}
                if new_manifest is not None else None),
            'development_requirements':required,
            'development_improvement_status':('PASS' if all(required.values()) else 'FAIL')
                if required is not None else 'DECODER_ONLY',
            'certification_authorized':False}
    output.mkdir(parents=True)
    (output/'comparison.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def audit_ablations(*,none,structured,consistency,combined,output):
    """Audit matched 2x2 G1 experiments; development only, never certification.

    The training runs are deliberately separate immutable checkpoints. The
    audit refuses unmatched ancestry, datasets, training schedules and seals.
    """
    paths={'none':none,'structured':structured,
           'consistency':consistency,'combined':combined}
    reports={}
    for name,path in paths.items():
        location=Path(path)
        file=location/'comparison.json' if location.is_dir() else location
        report=json.loads(file.read_text(encoding='utf-8'))
        if (report.get('schema')!='nanojev.gamer.graph.g1.controlled-comparison.v1'
            or report.get('split')!='dev' or report.get('blind_test_used') is not False
            or report.get('certification_authorized') is not False
            or not report.get('candidate_head_sha256')
            or report.get('candidate_training_controls') is None):
            raise ValueError('GAMER_GRAPH_ABLATION_INVALID_COMPARISON')
        reports[name]=report
    baseline=reports['none']
    if any(r['results']['greedy_baseline']!=baseline['results']['greedy_baseline'] or
           r['results']['global_decoder_only']!=baseline['results']['global_decoder_only']
           for r in reports.values()):
        raise ValueError('GAMER_GRAPH_ABLATION_BASELINE_EVALUATION_MISMATCH')
    matching=('baseline_dataset_sha256','baseline_head_sha256',
              'candidate_dataset_sha256','cases','split')
    training=('counterfactual_fraction','steps','accum','lr','seed','train_examples',
              'g1_refined_head_sha256','g1_microbatch_family_counts')
    if len({tuple(r[k] for k in matching) for r in reports.values()})!=1:
        raise ValueError('GAMER_GRAPH_ABLATION_BASELINE_DATASET_MISMATCH')
    for k in training:
        if len({json.dumps(r['candidate_training_controls'].get(k),sort_keys=True)
                for r in reports.values()})!=1:
            raise ValueError('GAMER_GRAPH_ABLATION_TRAINING_CONTROLS_MISMATCH')
    controls={
        'none':(0.0,0.0),'structured':(.40,0.0),
        'consistency':(0.0,.15),'combined':(.40,.15),
    }
    hashes=[]
    for name,report in reports.items():
        control=report['candidate_training_controls']
        if (control.get('g1_family_balanced') is not True or
            abs(control.get('structured_weight',-1)-controls[name][0])>1e-8 or
            abs(control.get('consistency_weight',-1)-controls[name][1])>1e-8 or
            not control.get('g1_microbatch_family_counts')):
            raise ValueError('GAMER_GRAPH_ABLATION_ARM_CONFIGURATION_MISMATCH')
        count=control['g1_microbatch_family_counts']
        if set(count)!={'encounter','allocation'} or count['encounter']!=count['allocation']:
            raise ValueError('GAMER_GRAPH_ABLATION_FAMILY_SAMPLING_IMBALANCED')
        hashes.append(report['candidate_head_sha256'])
    if len(set(hashes))!=4:
        raise ValueError('GAMER_GRAPH_ABLATION_DUPLICATE_CHECKPOINTS')
    def summary(metrics):
        return {**{key:metrics[key] for key in ('object_exact_rate','precision','recall','f1')},
                **{kind+'_recall':metrics['by_kind'][kind]['recall']
                   for kind in ('actor','action','quantity','outcome')}}
    def delta(a,b):
        return {k:round(a[k]-b[k],8) for k in a}
    base_metrics=summary(baseline['results']['greedy_baseline'])
    arm_metrics={name:summary(r['results']['global_plus_refinement'])
                 for name,r in reports.items()}
    report={'schema':'nanojev.gamer.graph.g1.ablation-audit.v1',
            'status':'DEV_ABLATION_AUDIT_ONLY_NOT_CERTIFICATION',
            'production_promotion':False,'g2_unlocked':False,'blind_test_used':False,
            'baseline':base_metrics,'arms':arm_metrics,
            'effect_structured_without_consistency':delta(arm_metrics['structured'],arm_metrics['none']),
            'effect_consistency_without_structured':delta(arm_metrics['consistency'],arm_metrics['none']),
            'effect_structured_with_consistency':delta(arm_metrics['combined'],arm_metrics['consistency']),
            'effect_consistency_with_structured':delta(arm_metrics['combined'],arm_metrics['structured']),
            'accepted_development_arms':[name for name,r in reports.items()
                if r['development_improvement_status']=='PASS'
                and r['development_requirements'] is not None
                and all(r['development_requirements'].values())],
            'source_comparison_paths':{name:str(path) for name,path in paths.items()}}
    out=Path(output)
    if out.exists():raise FileExistsError('GAMER_GRAPH_ABLATION_REPORT_IMMUTABLE')
    out.mkdir(parents=True)
    (out/'ablation_audit.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report
