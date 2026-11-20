"""G2-0084: read-only forensic attribution of the completed G2-0083 blind audit.

No training, model loading or new sampling.  This is exploratory diagnosis on
ALREADY scored labels, never prospective certification or a selection oracle.

Usage: python -m tools.nanojev_gamer_v1_g2_binding_forensics_0084
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from tools import nanojev_gamer_v1_g2_native_training_0083 as prior
from tools.nanojev_gamer_v1_graph_contract import BINDINGS

SCHEMA='nanojev.gamer.graph.g2.binding-forensics-0084.v1'
MODES=('trained','untrained','zero_hidden','no_binding')


def require(ok,code):
    if not ok:
        raise ValueError('G2_0084_'+code)


def _object_set(items):
    require(isinstance(items,list),'OBJECTS_NOT_LIST')
    out=set()
    for obj in items:
        require(isinstance(obj,list) and len(obj)==3 and
                type(obj[0]) is int and type(obj[1]) is int and
                0<=obj[0]<obj[1] and isinstance(obj[2],str), 'OBJECT_SHAPE')
        item=(obj[0],obj[1],obj[2])
        require(item not in out,'DUPLICATE_OBJECT')
        out.add(item)
    return out


def _score(tp,pred,gold):
    p=tp/pred if pred else 0.0
    r=tp/gold if gold else 0.0
    return {'precision':p,'recall':r,'f1':2*p*r/(p+r) if p+r else 0.0}


def _edge_anchor(edge):
    return (edge[0],edge[1]),(edge[2],edge[3])


def _obj_anchor(obj):
    return (obj[2],(obj[0],obj[1]))


def forensic_analysis(gold_rows,pred_rows):
    """Recompute binding errors by cause, including paired untrained controls."""
    require(len(gold_rows)==len(pred_rows)>0,'ROW_COUNT')
    gold_by={r['case_id']:r for r in gold_rows}
    pred_by={r['case_id']:r for r in pred_rows}
    require(len(gold_by)==len(gold_rows) and len(pred_by)==len(pred_rows) and
            gold_by.keys()==pred_by.keys(),'CASE_BIJECTION')
    require(all(r.get('split') in ('dev','challenge') for r in pred_rows),
            'PREDICTION_SCHEMA')
    result={};per_case=[]
    for split in ('dev','challenge'):
        count=Counter(); types={m:{kind:Counter() for kind in BINDINGS} for m in MODES}
        family={};conditioned=Counter();pair_counts=Counter()
        for item in pred_rows:
            if item['split']!=split:continue
            prior._check_prediction_row(item)
            gold=gold_by[item['case_id']]
            require(all(gold[k]==item[k] for k in ('split','family','prompt_sha256')),
                    'GOLD_PRED_BINDING')
            truth=prior._gold_edges(gold['graph'])
            gold_objects={(x['start'],x['end'],x['kind']) for x in gold['gold_objects']}
            require(len(gold_objects)==len(gold['gold_objects']),'GOLD_OBJECT_DUPLICATE')
            family.setdefault(item['family'],Counter())
            count['cases']+=1;count['gold_edges']+=len(truth)
            predicted={};obs={}
            for mode in MODES:
                row=item['modes'][mode]
                obs[mode]=_object_set(row['g1_objects'])
                predicted[mode]=prior._keyed_edges(row['binding_edges'])
                anchors={_obj_anchor(x) for x in obs[mode]}
                require(all(a in anchors and b in anchors for a,b in map(_edge_anchor,predicted[mode])),
                        'EDGE_WITHOUT_G1_ENDPOINT')
                count[mode+'_tp']+=len(truth&predicted[mode])
                count[mode+'_gold']+=len(truth)
                count[mode+'_pred']+=len(predicted[mode])
                count[mode+'_exact']+=int(predicted[mode]==truth)
                count[mode+'_nonempty']+=bool(predicted[mode])
                for edge in truth:
                    types[mode][edge[-1]]['gold']+=1
                    if edge in predicted[mode]:types[mode][edge[-1]]['tp']+=1
                for edge in predicted[mode]:types[mode][edge[-1]]['pred']+=1
            require(obs['trained']==obs['untrained']==obs['no_binding'],
                    'G1_CHANGED_BETWEEN_BINDING_ARMS')
            require(len(obs['trained'])==item['surface_count'],'SURFACE_COUNT')
            anchors={_obj_anchor(x) for x in obs['trained']}
            reachable={e for e in truth if all(a in anchors for a in _edge_anchor(e))}
            impossible=truth-reachable
            missed_reachable=reachable-predicted['trained']
            false=predicted['trained']-truth
            pair_to_gold={(e[0],e[1],e[2],e[3]):e[4] for e in truth}
            wrong_label={e for e in false if (e[0],e[1],e[2],e[3]) in pair_to_gold}
            correct_pair_labels={e for e in false if e not in wrong_label}
            require(len(false)==len(wrong_label)+len(correct_pair_labels),'ERROR_PARTITION')
            count['g1_exact']+=int(obs['trained']==gold_objects)
            count['all_gold_edges_reachable']+=int(not impossible)
            count['gold_unreachable']+=len(impossible)
            count['gold_reachable']+=len(reachable)
            count['missed_reachable']+=len(missed_reachable)
            count['false_edges']+=len(false)
            count['wrong_label_for_gold_endpoint_pair']+=len(wrong_label)
            count['extra_endpoint_pairs']+=len(correct_pair_labels)
            count['g2_correct_with_exact_g1']+=int(obs['trained']==gold_objects and predicted['trained']==truth)
            count['cases_with_missed_reachable']+=int(bool(missed_reachable))
            count['cases_with_extra_edges']+=int(bool(false))
            count['cases_with_unreachable_gold']+=int(bool(impossible))
            count['cases_with_all_gold_reachable_and_g2_failure']+=int(not impossible and predicted['trained']!=truth)
            count['cases_with_no_predicted_edges']+=int(not predicted['trained'])
            count['cases_all_gold_covered_by_trained']+=int(truth<=predicted['trained'])
            baseline_tp=len(truth&predicted['untrained'])
            trained_tp=len(truth&predicted['trained'])
            pair_counts['same_edge_set']+=int(predicted['trained']==predicted['untrained'])
            pair_counts['different_edge_set']+=int(predicted['trained']!=predicted['untrained'])
            pair_counts['tp_improved']+=int(trained_tp>baseline_tp)
            pair_counts['tp_worsened']+=int(trained_tp<baseline_tp)
            pair_counts['tp_unchanged']+=int(trained_tp==baseline_tp)
            pair_counts['edge_tp_net_gain']+=trained_tp-baseline_tp
            pair_counts['edge_count_net_change']+=len(predicted['trained'])-len(predicted['untrained'])
            pair_counts['exact_gain']+=int(predicted['trained']==truth and predicted['untrained']!=truth)
            pair_counts['exact_loss']+=int(predicted['trained']!=truth and predicted['untrained']==truth)
            group='exact_g1' if obs['trained']==gold_objects else 'inexact_g1'
            conditioned[group+'_cases']+=1
            conditioned[group+'_g2_exact']+=int(predicted['trained']==truth)
            conditioned[group+'_gold_edges']+=len(truth)
            conditioned[group+'_reachable_edges']+=len(reachable)
            family[item['family']]['cases']+=1
            family[item['family']]['g1_exact']+=int(obs['trained']==gold_objects)
            family[item['family']]['g2_exact']+=int(predicted['trained']==truth)
            family[item['family']]['gold_edges']+=len(truth)
            family[item['family']]['edge_tp']+=trained_tp
            family[item['family']]['edge_pred']+=len(predicted['trained'])
            per_case.append({'case_id':item['case_id'],'split':split,'family':item['family'],
                'g1_exact':obs['trained']==gold_objects,'g2_exact':predicted['trained']==truth,
                'gold_edges':len(truth),'gold_edges_unreachable':len(impossible),
                'missed_reachable_edges':len(missed_reachable),'false_edges':len(false),
                'wrong_labels_for_gold_pairs':len(wrong_label),
                'new_endpoint_pair_false_edges':len(correct_pair_labels),
                'trained_edge_tp':trained_tp,'untrained_edge_tp':baseline_tp,
                'trained_edge_count':len(predicted['trained']),
                'untrained_edge_count':len(predicted['untrained'])})
        require(count['cases']>0,'EMPTY_'+split.upper())
        assert count['gold_edges']==count['gold_reachable']+count['gold_unreachable']
        summary=dict(count)
        summary['modes']={m:{'exact':count[m+'_exact'],'exact_rate':count[m+'_exact']/count['cases'],
            'tp':count[m+'_tp'],'pred':count[m+'_pred'],'gold':count[m+'_gold'],
            'nonempty_cases':count[m+'_nonempty'],
            **_score(count[m+'_tp'],count[m+'_pred'],count[m+'_gold'])} for m in MODES}
        summary['binding_by_type']={m:{k:dict(v,**_score(v['tp'],v['pred'],v['gold']))
                                        for k,v in kinds.items()} for m,kinds in types.items()}
        summary['paired_vs_untrained']=dict(pair_counts)
        summary['conditioned_on_g1']=dict(conditioned)
        summary['by_family']={f:dict(v,**_score(v['edge_tp'],v['edge_pred'],v['gold_edges']))
                              for f,v in family.items()}
        summary['reachable_gold_edge_recall']=((count['gold_reachable']-count['missed_reachable']) /
                 count['gold_reachable'] if count['gold_reachable'] else 0.0)
        result[split]=summary
    return {'schema':SCHEMA,'status':'POST_HOC_DIAGNOSIS_ONLY_NOT_CERTIFICATION',
            'interpretation_boundary':'Reuses already-scored G2-0083 gold answers. No new blind evidence.',
            'splits':result},per_case


def run(*,root='../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083',
        output='../runtime/reports/gamer-v1/oracle-fabric-g2-binding-forensics-0084'):
    root=Path(root).resolve();output=Path(output).resolve()
    require(not output.exists(),'OUTPUT_IMMUTABLE')
    paths=prior.defaults(root.parent)
    proof=prior.check_seal(paths,root)
    locked=prior.js(root/'blind'/'lock.json')
    require(locked.get('schema')==prior.SCHEMA and
            locked.get('status')=='BLIND_G2_NATIVE_PREDICTIONS_LOCKED' and
            locked.get('predictions_sha256')==prior.sha(root/'blind'/'predictions.jsonl') and
            locked.get('prompts_sha256')==proof['prompts_sha256'] and
            locked.get('gold_read_during_inference') is False and
            locked.get('teacher_forced_g1_spans') is False and
            locked.get('g2_head_sha256')==prior.sha(root/'training'/prior.cur.HEAD_FILE),
            'BLIND_LINEAGE')
    scored=prior.js(root/'prospective'/'report.json')
    require(scored.get('schema')==prior.SCHEMA and
            scored.get('status')=='PROSPECTIVE_G2_BINDING_GATE_BLOCKED' and
            scored.get('g2_prospective_pass') is False,'NOT_SCORED_FAILED_G2_0083')
    gold=prior.load_jsonl(root/'seal'/'answers.SECRET.jsonl')
    pred=prior.load_jsonl(root/'blind'/'predictions.jsonl')
    audit,cases=forensic_analysis(gold,pred)
    require(len(cases)==prior.DEV+prior.CHALLENGE,'CASE_COUNT')
    for split in ('dev','challenge'):
        m=audit['splits'][split]['modes']['trained']
        existing=scored['splits'][split]['metrics']['trained']
        require(m['exact']==existing['exact'] and
                abs(m['f1']-existing['edge_f1'])<1e-10 and
                abs(m['precision']-existing['edge_precision'])<1e-10 and
                abs(m['recall']-existing['edge_recall'])<1e-10,
                'INDEPENDENT_METRIC_RECOMPUTATION_'+split)
        require(audit['splits'][split]['gold_unreachable']==
                scored['splits'][split]['gold_edges_unreachable_from_predicted_g1'],
                'UNREACHABLE_EDGE_DISAGREEMENT_'+split)
    audit['source_integrity']={
        'blind_prediction_sha256':locked['predictions_sha256'],
        'gold_answer_sha256':proof['answers_sha256'],
        'trained_head_sha256':locked['g2_head_sha256'],
        'prior_score_sha256':prior.sha(root/'prospective'/'report.json'),
        'independent_metric_recomputation':'PASS',
        'train_called':False,'checkpoint_modified':False,'new_oracle_labels_used':False}
    output.mkdir(parents=True,exist_ok=False)
    prior.write_json(output/'report.json',audit)
    prior.write_jsonl(output/'cases.jsonl',cases)
    print('G2_0084_DIAGNOSIS '+json.dumps({split:{
        'g1_exact':audit['splits'][split]['g1_exact'],
        'g2_exact':audit['splits'][split]['modes']['trained']['exact'],
        'untrained_edge_f1':audit['splits'][split]['modes']['untrained']['f1'],
        'trained_edge_f1':audit['splits'][split]['modes']['trained']['f1'],
        'unreachable_gold_edges':audit['splits'][split]['gold_unreachable'],
        'missed_reachable_gold_edges':audit['splits'][split]['missed_reachable'],
        'wrong_labels_for_gold_pairs':audit['splits'][split]['wrong_label_for_gold_endpoint_pair'],
        'extra_endpoint_pair_edges':audit['splits'][split]['extra_endpoint_pairs'],
        'trained_vs_untrained':audit['splits'][split]['paired_vs_untrained']}
        for split in ('dev','challenge')},sort_keys=True),flush=True)
    return audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='../runtime/reports/gamer-v1/oracle-fabric-g2-native-training-0083')
    parser.add_argument('--output',default='../runtime/reports/gamer-v1/oracle-fabric-g2-binding-forensics-0084')
    return run(**vars(parser.parse_args(argv)))


if __name__=='__main__':main()
