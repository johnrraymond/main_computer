"""Read-only, same-prompt G1 attribution: greedy baseline / global / newly trained.

Only evaluates the development split; it cannot certify any checkpoint or
inspect the sealed test split. Uses one frozen Qwen feature capture per prompt.
"""
from __future__ import annotations

from collections import Counter, defaultdict
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



def _object_transitions(baseline_records, candidate_records, dev_rows):
    """Gold-relative flips with identical global decoding on both checkpoints.

    An action's regression is counted only if the old checkpoint got the exact
    author-marked span/type right and the new checkpoint does not.  No labels
    influence inference; labels enter this read-only development audit only.
    """
    totals={kind:Counter() for kind in SPAN_KINDS}
    families=defaultdict(lambda:{kind:Counter() for kind in SPAN_KINDS})
    action_cases=[]
    if not (len(baseline_records)==len(candidate_records)==len(dev_rows)):
        raise ValueError('GAMER_GRAPH_TRANSITION_CASE_COUNT_MISMATCH')
    for index,((old,old_gold),(new,new_gold),row) in enumerate(
            zip(baseline_records,candidate_records,dev_rows),1):
        if old_gold!=new_gold:
            raise ValueError('GAMER_GRAPH_TRANSITION_GOLD_MISMATCH')
        family=row['family']
        for kind in SPAN_KINDS:
            gold={x for x in old_gold if x[2]==kind}
            old_kind={x for x in old if x[2]==kind}
            new_kind={x for x in new if x[2]==kind}
            lost=(old_kind & gold)-(new_kind & gold)
            gained=(new_kind & gold)-(old_kind & gold)
            counted={'lost_correct':len(lost),'gained_correct':len(gained),
                     'unchanged_correct':len(old_kind & new_kind & gold),
                     'introduced_false_positives':len((new_kind-gold)-(old_kind-gold)),
                     'removed_false_positives':len((old_kind-gold)-(new_kind-gold))}
            totals[kind].update(counted)
            families[family][kind].update(counted)
            if kind=='action' and (lost or gained):
                def surface(span):
                    a,b,k=span
                    return {'start':a,'end':b,'kind':k,'surface':row['prompt'][a:b]}
                loss_cases=[]
                for span in sorted(lost):
                    a,b,_=span
                    overlap=[surface(q) for q in sorted(new)
                             if q[0]<b and q[1]>a]
                    loss_cases.append({**surface(span),'candidate_overlaps':overlap})
                action_cases.append({'index':index,'family':family,
                                     'lost_correct':loss_cases,
                                     'gained_correct':[surface(x) for x in sorted(gained)]})
    def summarize(kind_counts):
        result={k:int(kind_counts.get(k,0)) for k in
                ('lost_correct','gained_correct','unchanged_correct',
                 'introduced_false_positives','removed_false_positives')}
        result['net_correct']=result['gained_correct']-result['lost_correct']
        return result
    return {'schema':'nanojev.gamer.graph.g1.gold-relative-transitions.v1',
            'same_decoder':'global_for_both',
            'by_kind':{kind:summarize(totals[kind]) for kind in SPAN_KINDS},
            'by_family':{family:{kind:summarize(counts[kind]) for kind in SPAN_KINDS}
                         for family,counts in sorted(families.items())},
            'action_regression_cases':action_cases}


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
    transitions=(_object_transitions(results['global_decoder_only'],
                    results['global_plus_refinement'],dev)
                 if new_head is not None else None)
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
                    'steps','accum','lr','seed','train_examples','g1_refined_head_sha256',
                    'g1_controlled_exposure','g1_replay_stream_sha256',
                    'g1_pair_stream_sha256','g1_parent_dataset_sha256',
                    'parent_retention_weight','hard_null_weight','g1_parent_retention_anchor_count',
                    'g1_parent_retention_replay_items')}
                if new_manifest is not None else None),
            'object_transitions':({k:v for k,v in transitions.items()
                                   if k!='action_regression_cases'}
                                  if transitions is not None else None),
            'development_requirements':required,
            'development_improvement_status':('PASS' if all(required.values()) else 'FAIL')
                if required is not None else 'DECODER_ONLY',
            'certification_authorized':False}
    output.mkdir(parents=True)
    (output/'comparison.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    if transitions is not None:
        (output/'action_regressions.jsonl').write_text(''.join(
            json.dumps(item,sort_keys=True,ensure_ascii=False)+'\n'
            for item in transitions['action_regression_cases']),encoding='utf-8')
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


def audit_exposure(*,replay,fraction_10,fraction_25,fraction_50,output):
    """Audit G1 continued-training drift versus counterfactual interference.

    The replay control must use the original dataset and zero counterfactual
    gradient exposure.  Other arms must use an identical verified train-only
    extension, while preserving the same original evaluation prompts.
    No blind test, stage gate, or production operation is available here.
    """
    paths={'replay':replay,'fraction_10':fraction_10,
           'fraction_25':fraction_25,'fraction_50':fraction_50}
    fractions={'replay':0.0,'fraction_10':0.10,
               'fraction_25':0.25,'fraction_50':0.50}
    out=Path(output)
    if out.exists():
        raise FileExistsError('GAMER_GRAPH_EXPOSURE_AUDIT_IMMUTABLE')
    reports={}
    for arm,path in paths.items():
        f=Path(path)
        report=json.loads((f/'comparison.json' if f.is_dir() else f).read_text(encoding='utf-8'))
        if (report.get('schema')!='nanojev.gamer.graph.g1.controlled-comparison.v1'
            or report.get('split')!='dev' or report.get('blind_test_used') is not False
            or report.get('certification_authorized') is not False
            or report.get('production_promotion') is not False
            or report.get('candidate_head_sha256') is None
            or report.get('candidate_training_controls') is None
            or report.get('object_transitions',{}).get('same_decoder')!='global_for_both'):
            raise ValueError('GAMER_GRAPH_EXPOSURE_INVALID_COMPARISON')
        reports[arm]=report
    base=reports['replay']
    baseline_hash=base['baseline_dataset_sha256']
    source_metrics=('greedy_baseline','global_decoder_only')
    if any(r['baseline_dataset_sha256']!=baseline_hash or
           r['baseline_head_sha256']!=base['baseline_head_sha256'] or
           r['cases']!=base['cases'] or
           any(r['results'][m]!=base['results'][m] for m in source_metrics)
           for r in reports.values()):
        raise ValueError('GAMER_GRAPH_EXPOSURE_BASELINE_MISMATCH')
    extension_sha=reports['fraction_10']['candidate_dataset_sha256']
    if (base['candidate_dataset_sha256']!=baseline_hash or
        not extension_sha or extension_sha==baseline_hash or
        any(reports[a]['candidate_dataset_sha256']!=extension_sha
            for a in ('fraction_10','fraction_25','fraction_50'))):
        raise ValueError('GAMER_GRAPH_EXPOSURE_DATASET_LINEAGE_MISMATCH')
    if len({r['candidate_head_sha256'] for r in reports.values()})!=4:
        raise ValueError('GAMER_GRAPH_EXPOSURE_DUPLICATE_HEAD')
    controls={a:r['candidate_training_controls'] for a,r in reports.items()}
    common=('steps','accum','lr','seed','g1_refined_head_sha256',
            'g1_replay_stream_sha256','g1_family_balanced',
            'g1_controlled_exposure','structured_weight','consistency_weight')
    for key in common:
        if len({json.dumps(c.get(key),sort_keys=True) for c in controls.values()})!=1:
            raise ValueError(f'GAMER_GRAPH_EXPOSURE_UNMATCHED_{key.upper()}')
    reference=controls['replay']
    if (not reference['g1_family_balanced'] or not reference['g1_controlled_exposure'] or
            reference['structured_weight']!=0 or reference['consistency_weight']!=0 or
            not reference['g1_replay_stream_sha256'] or
            reference['g1_refined_head_sha256']!=base['baseline_head_sha256'] or
            reference['steps']<=0 or reference['accum']<=0):
        raise ValueError('GAMER_GRAPH_EXPOSURE_INVALID_COMMON_CONTROLS')
    pair_stream=controls['fraction_10']['g1_pair_stream_sha256']
    if not pair_stream or any(controls[a].get('g1_pair_stream_sha256')!=pair_stream
                            for a in ('fraction_25','fraction_50')):
        raise ValueError('GAMER_GRAPH_EXPOSURE_UNMATCHED_PAIR_STREAM')
    if controls['replay']['g1_pair_stream_sha256'] is not None:
        raise ValueError('GAMER_GRAPH_EXPOSURE_CONTROL_CONTAINS_PAIRS')
    for arm,r in reports.items():
        c=r['candidate_training_controls']
        if abs(float(c['counterfactual_fraction'])-fractions[arm])>1e-9:
            raise ValueError('GAMER_GRAPH_EXPOSURE_FRACTION_MISMATCH')
        if (arm=='replay' and c['g1_parent_dataset_sha256'] is not None or
            arm!='replay' and c['g1_parent_dataset_sha256']!=baseline_hash):
            raise ValueError('GAMER_GRAPH_EXPOSURE_PARENT_LINEAGE_MISMATCH')
        counts=c['g1_microbatch_family_counts']
        if set(counts)!={'encounter','allocation'}:
            raise ValueError('GAMER_GRAPH_EXPOSURE_FAMILY_MISMATCH')
        total=0
        for family,values in counts.items():
            if set(values)!={'counterfactual','replay'}:
                raise ValueError('GAMER_GRAPH_EXPOSURE_COUNT_MISMATCH')
            slots=sum(values.values())
            expected_cf=int(slots*fractions[arm]+0.5)
            if values['counterfactual']!=expected_cf:
                raise ValueError('GAMER_GRAPH_EXPOSURE_SAMPLING_COUNT_MISMATCH')
            total+=slots
        if (counts['encounter']!=counts['allocation'] or
            total!=reference['steps']*reference['accum']):
            raise ValueError('GAMER_GRAPH_EXPOSURE_FAMILY_IMBALANCED')
        if arm=='replay' and c['train_examples'] >= controls['fraction_10']['train_examples']:
            raise ValueError('GAMER_GRAPH_EXPOSURE_REPLAY_NOT_PARENT_ONLY')
        candidate=r['results']['global_plus_refinement']
        baseline=r['results']['global_decoder_only']
        for kind in ('actor','action','quantity','outcome'):
            d=r['object_transitions']['by_kind'][kind]
            delta=candidate['by_kind'][kind]['correct']-baseline['by_kind'][kind]['correct']
            if d['net_correct']!=delta or d['net_correct']!=d['gained_correct']-d['lost_correct']:
                raise ValueError('GAMER_GRAPH_EXPOSURE_TRANSITION_MISMATCH')
    def short(m):
        return {**{key:m[key] for key in ('object_exact_rate','precision','recall','f1')},
                **{kind+'_recall':m['by_kind'][kind]['recall']
                   for kind in ('actor','action','quantity','outcome')}}
    old=short(base['results']['global_decoder_only'])
    arms={a:short(r['results']['global_plus_refinement']) for a,r in reports.items()}
    replay_drift={k:round(arms['replay'][k]-old[k],8) for k in old}
    vs_replay={a:{k:round(v[k]-arms['replay'][k],8) for k in old}
               for a,v in arms.items() if a!='replay'}
    report={'schema':'nanojev.gamer.graph.g1.exposure-audit.v1',
            'status':'DEV_CONTINUATION_EXPOSURE_AUDIT_ONLY_NOT_CERTIFICATION',
            'production_promotion':False,'g2_unlocked':False,'blind_test_used':False,
            'baseline_head_sha256':base['baseline_head_sha256'],
            'baseline_dataset_sha256':baseline_hash,
            'extension_dataset_sha256':extension_sha,
            'matched_replay_stream_sha256':reference['g1_replay_stream_sha256'],
            'matched_pair_stream_sha256':pair_stream,
            'common_training_controls':{k:reference[k] for k in common if k not in
                                        ('g1_replay_stream_sha256','g1_family_balanced',
                                         'g1_controlled_exposure')},
            'baseline_global':old,'arms':arms,
            'replay_continuation_effect':replay_drift,
            'counterfactual_effect_relative_to_replay':vs_replay,
            'action_transitions':{
                a:{'by_family':{family:counts['action']
                                for family,counts in r['object_transitions']['by_family'].items()},
                   'total':r['object_transitions']['by_kind']['action']}
                for a,r in reports.items()},
            'accepted_development_arms':[
                a for a,r in reports.items()
                if r['development_improvement_status']=='PASS' and
                   r.get('development_requirements') and
                   all(r['development_requirements'].values())],
            'source_comparison_paths':{a:str(p) for a,p in paths.items()}}
    out.mkdir(parents=True)
    (out/'exposure_audit.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',
                                           encoding='utf-8')
    return report


def audit_retention(*,control,retained,output):
    """Compare matched G1 continuation runs, with and without parent anchoring.

    No model is loaded and no sealed evaluation data are touched. Training and
    evaluation identity, stream identity and parent lineage must all agree.
    """
    destination=Path(output)
    if destination.exists():
        raise FileExistsError('GAMER_GRAPH_RETENTION_AUDIT_IMMUTABLE')
    def report_at(value):
        path=Path(value)
        return json.loads((path/'comparison.json' if path.is_dir() else path)
                          .read_text(encoding='utf-8'))
    a=report_at(control);b=report_at(retained)
    for report in (a,b):
        if (report.get('schema')!='nanojev.gamer.graph.g1.controlled-comparison.v1' or
            report.get('status')!='DEVELOPMENT_COMPARISON_ONLY_NOT_CERTIFICATION' or
            report.get('split')!='dev' or
            report.get('blind_test_used') is not False or
            report.get('certification_authorized') is not False or
            report.get('production_promotion') is not False or
            not report.get('candidate_head_sha256') or
            not report.get('candidate_training_controls') or
            report.get('object_transitions',{}).get('same_decoder')!='global_for_both'):
            raise ValueError('GAMER_GRAPH_RETENTION_INVALID_COMPARISON')
    same=('baseline_head_sha256','baseline_dataset_sha256','candidate_dataset_sha256',
          'cases')
    if any(a[k]!=b[k] for k in same) or any(
            a['results'][k]!=b['results'][k]
            for k in ('greedy_baseline','global_decoder_only')):
        raise ValueError('GAMER_GRAPH_RETENTION_BASELINE_MISMATCH')
    if a['candidate_head_sha256']==b['candidate_head_sha256']:
        raise ValueError('GAMER_GRAPH_RETENTION_DUPLICATE_CHECKPOINT')
    ca=a['candidate_training_controls'];cb=b['candidate_training_controls']
    common=('structured_weight','consistency_weight','g1_family_balanced',
            'g1_microbatch_family_counts','counterfactual_fraction',
            'steps','accum','lr','seed','train_examples',
            'g1_refined_head_sha256','g1_controlled_exposure',
            'g1_replay_stream_sha256','g1_pair_stream_sha256',
            'g1_parent_dataset_sha256')
    for name in common:
        if ca.get(name)!=cb.get(name):
            raise ValueError(f'GAMER_GRAPH_RETENTION_UNMATCHED_{name.upper()}')
    if (ca.get('parent_retention_weight')!=0 or
        not isinstance(cb.get('parent_retention_weight'),(int,float)) or
        cb['parent_retention_weight']<=0 or
        ca['g1_refined_head_sha256']!=a['baseline_head_sha256'] or
        not ca['g1_controlled_exposure'] or not ca['g1_family_balanced'] or
        ca['structured_weight']!=0 or ca['consistency_weight']!=0 or
        not ca['g1_replay_stream_sha256'] or
        cb.get('g1_parent_retention_anchor_count',0)<=0 or
        cb.get('g1_parent_retention_replay_items',0)<=0 or
        ca.get('g1_parent_retention_anchor_count',0)!=0 or
        ca.get('g1_parent_retention_replay_items',0)!=0):
        raise ValueError('GAMER_GRAPH_RETENTION_INVALID_CONTROL_SETTINGS')
    for report in (a,b):
        measured=report['results']['global_plus_refinement']
        baseline=report['results']['global_decoder_only']
        for kind in ('actor','action','quantity','outcome'):
            t=report['object_transitions']['by_kind'][kind]
            if (t['net_correct']!=t['gained_correct']-t['lost_correct'] or
                t['net_correct']!=measured['by_kind'][kind]['correct']-
                baseline['by_kind'][kind]['correct']):
                raise ValueError('GAMER_GRAPH_RETENTION_TRANSITION_MISMATCH')
    def metrics(report):
        m=report['results']['global_plus_refinement']
        return {'exact_prompts':m['exact_prompts'],'f1':m['f1'],
                'precision':m['precision'],'recall':m['recall'],
                'by_kind':{kind:m['by_kind'][kind]
                           for kind in ('actor','action','quantity','outcome')},
                'action_transitions':report['object_transitions']['by_kind']['action']}
    control_m=metrics(a);retained_m=metrics(b)
    result={'schema':'nanojev.gamer.graph.g1.parent-retention-audit.v1',
            'status':'DEV_RETENTION_AUDIT_ONLY_NOT_CERTIFICATION',
            'production_promotion':False,'g2_unlocked':False,'blind_test_used':False,
            'baseline_head_sha256':a['baseline_head_sha256'],
            'candidate_dataset_sha256':a['candidate_dataset_sha256'],
            'common_training_controls':{key:ca.get(key) for key in common},
            'control':control_m,'retained':retained_m,
            'retention_weight':cb['parent_retention_weight'],
            'parent_correct_spans_anchored':cb['g1_parent_retention_anchor_count'],
            'retention_improved_f1':retained_m['f1']>control_m['f1'],
            'retention_reduced_action_losses':
                 retained_m['action_transitions']['lost_correct']<
                 control_m['action_transitions']['lost_correct'],
            'development_acceptance':{
                'control':a['development_improvement_status'],
                'retained':b['development_improvement_status']},
            'sources':{'control':str(control),'retained':str(retained)}}
    destination.mkdir(parents=True)
    (destination/'retention_audit.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',
                                                   encoding='utf-8')
    return result


def audit_g1_missed_span_objectives(*,control,structured,null_focused,output,
                                     baseline_diagnosis=None,control_diagnosis=None,
                                     structured_diagnosis=None,null_focused_diagnosis=None):
    """Read-only three-arm comparison on identical dev prompts and train streams.

    Frozen G1-0007 parent retention must be active in all three arms. The
    control, structured-set, and hard-null arms differ in exactly ONE
    loss weight. Neither scoring nor this audit has a promotion path.
    """
    paths={'control':control,'structured':structured,'null_focused':null_focused}
    dest=Path(output)
    if dest.exists():
        raise FileExistsError('GAMER_GRAPH_MISS_OBJECTIVE_AUDIT_IMMUTABLE')
    reports={}
    for name,path in paths.items():
        f=Path(path)
        r=json.loads((f/'comparison.json' if f.is_dir() else f).read_text(encoding='utf-8'))
        if (r.get('schema')!='nanojev.gamer.graph.g1.controlled-comparison.v1' or
            r.get('status')!='DEVELOPMENT_COMPARISON_ONLY_NOT_CERTIFICATION' or
            r.get('split')!='dev' or r.get('blind_test_used') is not False or
            r.get('certification_authorized') is not False or
            r.get('production_promotion') is not False or
            not r.get('candidate_head_sha256') or
            not isinstance(r.get('candidate_training_controls'),dict) or
            r.get('object_transitions',{}).get('same_decoder')!='global_for_both'):
            raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_INVALID_COMPARISON')
        reports[name]=r
    base=reports['control']
    for name,r in reports.items():
        if (any(r.get(k)!=base.get(k) for k in
                ('baseline_head_sha256','baseline_dataset_sha256',
                 'candidate_dataset_sha256','cases','split')) or
            any(r['results'].get(k)!=base['results'].get(k)
                for k in ('greedy_baseline','global_decoder_only'))):
            raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_BASELINE_MISMATCH')
    if len({r['candidate_head_sha256'] for r in reports.values()})!=3:
        raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_DUPLICATE_HEAD')
    ref=base['candidate_training_controls']
    shared=('steps','accum','lr','seed','train_examples',
            'g1_refined_head_sha256','g1_parent_dataset_sha256',
            'counterfactual_fraction','consistency_weight',
            'parent_retention_weight','g1_controlled_exposure',
            'g1_family_balanced','g1_replay_stream_sha256',
            'g1_pair_stream_sha256','g1_microbatch_family_counts',
            'g1_parent_retention_anchor_count','g1_parent_retention_replay_items')
    for name,r in reports.items():
        c=r['candidate_training_controls']
        for k in shared:
            if c.get(k)!=ref.get(k):
                raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_UNMATCHED_'+k.upper())
        for kind in ('actor','action','quantity','outcome'):
            measured=r['results']['global_plus_refinement']['by_kind'][kind]['correct']
            baseline=r['results']['global_decoder_only']['by_kind'][kind]['correct']
            t=r['object_transitions']['by_kind'][kind]
            if (measured-baseline!=t['net_correct'] or
                t['net_correct']!=t['gained_correct']-t['lost_correct']):
                raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_TRANSITION_MISMATCH')
    if (not ref.get('g1_controlled_exposure') or
        not ref.get('g1_family_balanced') or
        not ref.get('g1_replay_stream_sha256') or
        not ref.get('g1_pair_stream_sha256') or
        ref.get('g1_refined_head_sha256')!=base['baseline_head_sha256'] or
        ref.get('consistency_weight')!=0 or
        ref.get('parent_retention_weight',0)<=0 or
        ref.get('counterfactual_fraction')!=.25 or
        ref.get('g1_parent_retention_anchor_count',0)<=0 or
        ref.get('g1_parent_retention_replay_items',0)<=0 or
        ref.get('steps',0)<=0 or ref.get('accum',0)<=0 or
        set(ref.get('g1_microbatch_family_counts') or ())!={'encounter','allocation'}):
        raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_INVALID_SHARED_SETTINGS')
    weights={name:(r['candidate_training_controls'].get('structured_weight'),
                   r['candidate_training_controls'].get('hard_null_weight'))
             for name,r in reports.items()}
    if (weights['control']!=(0.0,0.0) or
        weights['structured']!=(.4,0.0) or
        weights['null_focused']!=(0.0,1.0)):
        raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_ARM_WEIGHTS_MISMATCH')
    def concise(r):
        m=r['results']['global_plus_refinement']
        return {'exact_prompts':m['exact_prompts'],
                'object_exact_rate':m['object_exact_rate'],
                'f1':m['f1'],'precision':m['precision'],'recall':m['recall'],
                'by_kind':m['by_kind'],
                'object_transitions':r['object_transitions']['by_kind'],
                'action_transitions_by_family':{
                    fam:types['action'] for fam,types
                    in r['object_transitions']['by_family'].items()},
                'development_improvement_status':r['development_improvement_status'],
                'development_requirements':r['development_requirements']}
    compact={name:concise(r) for name,r in reports.items()}
    metrics=('exact_prompts','f1','precision','recall')
    effects={name:{k:round(compact[name][k]-compact['control'][k],8)
                   for k in metrics} for name in ('structured','null_focused')}
    answer={'schema':'nanojev.gamer.graph.g1.missed-span-objective-audit.v1',
            'status':'DEV_MISS_OBJECTIVE_AUDIT_ONLY_NOT_CERTIFICATION',
            'blind_test_used':False,'production_promotion':False,'g2_unlocked':False,
            'baseline_head_sha256':base['baseline_head_sha256'],
            'candidate_dataset_sha256':base['candidate_dataset_sha256'],
            'common_training_controls':{k:ref[k] for k in shared},
            'arms':compact,'effects_relative_to_control':effects,
            'accepted_development_arms':[n for n,r in reports.items()
                if r['development_improvement_status']=='PASS' and
                   r.get('development_requirements') and
                   all(r['development_requirements'].values())],
            'source_comparison_paths':{n:str(p) for n,p in paths.items()}}
    diagnostic_inputs={'baseline':baseline_diagnosis,
                       'control':control_diagnosis,
                       'structured':structured_diagnosis,
                       'null_focused':null_focused_diagnosis}
    if any(value is not None for value in diagnostic_inputs.values()):
        if not all(value is not None for value in diagnostic_inputs.values()):
            raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_INCOMPLETE_DIAGNOSES')
        diagnoses={};miss_sets={}
        for arm,value in diagnostic_inputs.items():
            source=Path(value)
            folder=source if source.is_dir() else source.parent
            data=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
            expected_head=(base['baseline_head_sha256'] if arm=='baseline' else
                           reports[arm]['candidate_head_sha256'])
            expected_data=(base['baseline_dataset_sha256'] if arm=='baseline' else
                           base['candidate_dataset_sha256'])
            if (data.get('schema')!='nanojev.gamer.graph.g1.span-attribution.v1' or
                data.get('status')!='DIAGNOSTIC_ONLY_NOT_A_GATE' or
                data.get('stage')!='G1' or data.get('split')!='dev' or
                data.get('limited') is not False or
                data.get('checkpoint_head_sha256')!=expected_head or
                data.get('dataset_sha256')!=expected_data or
                data.get('cases')!=base['cases'] or
                data.get('test_or_challenge_split_used') is not False or
                data.get('certification_authorized') is not False or
                data.get('checkpoint_mutated') is not False):
                raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_DIAGNOSIS_LINEAGE')
            metrics=(base['results']['global_decoder_only'] if arm=='baseline' else
                     reports[arm]['results']['global_plus_refinement'])
            measured=data['threshold_sweep']['0.50']
            if any(measured[k]!=metrics[v] for k,v in
                   (('correct','correct'),('predicted','predicted'),
                    ('expected','expected'))) or measured['exact_rate']!=metrics['object_exact_rate']:
                raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_DIAGNOSIS_METRIC_MISMATCH')
            keys=set()
            with (folder/'misses.jsonl').open(encoding='utf-8') as file:
                for line in file:
                    entry=json.loads(line)
                    key=(entry['index'],tuple(entry['gold_span']),entry['kind'])
                    if key in keys or not 1<=entry['index']<=base['cases']:
                        raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_DUPLICATE_DIAG_MISS')
                    keys.add(key)
            if (len(keys)!=sum(data['miss_reasons'].values()) or
                sum(data['miss_reasons'].values())!=metrics['expected']-metrics['correct']):
                raise ValueError('GAMER_GRAPH_MISS_OBJECTIVE_DIAGNOSIS_MISS_COUNT')
            diagnoses[arm]=data
            miss_sets[arm]=keys
        baseline_misses=miss_sets['baseline']
        answer['diagnostic_comparison']={
            'baseline_miss_reasons':diagnoses['baseline']['miss_reasons'],
            'arms':{
                arm:{'miss_reasons':diagnoses[arm]['miss_reasons'],
                     'by_kind_miss_reasons':diagnoses[arm]['by_kind_miss_reasons'],
                     'original_misses_recovered':len(baseline_misses-miss_sets[arm]),
                     'newly_missed_vs_parent':len(miss_sets[arm]-baseline_misses),
                     'original_misses_recovered_by_kind':{
                         kind:sum(key[2]==kind for key in baseline_misses-miss_sets[arm])
                         for kind in ('actor','action','quantity','outcome')},
                     'newly_missed_by_kind':{
                         kind:sum(key[2]==kind for key in miss_sets[arm]-baseline_misses)
                         for kind in ('actor','action','quantity','outcome')}}
                for arm in paths},
            'source_diagnostic_paths':{arm:str(value)
                                       for arm,value in diagnostic_inputs.items()}}
    dest.mkdir(parents=True)
    (dest/'missed_span_objective_audit.json').write_text(
        json.dumps(answer,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return answer
