#!/usr/bin/env python3
"""Read-only causal substitution audit for GAMER V1 flat register predictions.

Consumes a COMPLETED flat-registers v2 run, not a model checkpoint. The
existing predicted register values and the certified training-derived formula
are the baseline. Development-set oracle register values are substituted ONLY
for diagnostic counterfactuals. No training, source modification, or oracle
values in the learned inference path.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
import csv
import hashlib
import json
import math
from pathlib import Path

from tools.nanojev_gamer_v1_flat_registers import _eval_plan

SCHEMA = 'nanojev.gamer.v1.flat-register-attribution.v1'
ZONES = ('zero', 'small_negative', 'small_positive', 'outer_negative', 'outer_positive')
CASE_FIELDS = ('case_sha256','game_id','predicted_game_id','target_zone','target',
               'predicted_answer','learned_route_error')
REGISTER_FIELDS = ('case_sha256','game_id','register_id','target','prediction')


def _sha_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path):
    with Path(path).open('r',encoding='utf-8') as f:return json.load(f)


def _rows(path):
    with Path(path).open('r',encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def _finite(value,name):
    if value is None:raise ValueError('GAMER_ATTRIBUTION_MISSING_VALUE: '+name)
    try: x=float(value)
    except (ValueError,TypeError) as exc:raise ValueError('GAMER_ATTRIBUTION_INVALID_VALUE: '+name) from exc
    if not math.isfinite(x):raise ValueError('GAMER_ATTRIBUTION_NONFINITE_VALUE: '+name)
    return x


def _require_cols(fields,required,path):
    if not fields or not set(required)<=set(fields):
        raise ValueError('GAMER_ATTRIBUTION_MISSING_COLUMNS: '+str(path))


def _source(flat_run):
    """Verify saved evaluation evidence and refuse stale/incompatible runs."""
    base=Path(flat_run).resolve()
    if not base.is_dir():raise FileNotFoundError('GAMER_ATTRIBUTION_FLAT_RUN_MISSING: '+str(base))
    names=('final_report.json','train_formula_templates.json',
           'dev_predictions.jsonl','dev_register_predictions.csv')
    required={name:base/name for name in names}
    for name,path in required.items():
        if not path.is_file():raise FileNotFoundError('GAMER_ATTRIBUTION_MISSING_ARTIFACT: '+name)
    source_hashes={name:_sha_file(path) for name,path in required.items()}
    report=_load_json(required['final_report.json'])
    if report.get('schema')!='nanojev.gamer.v1.flat-register-training.v2' or report.get('status')!='TRAINED_DIAGNOSTIC_NOT_PROMOTED':
        raise ValueError('GAMER_ATTRIBUTION_REQUIRES_COMPLETED_FLAT_V2')
    if (Path(report.get('output','')).resolve()!=base or not report.get('source',{}).get('dataset_sha256')
            or not report.get('source',{}).get('frozen_adapter_sha256')):
        raise ValueError('GAMER_ATTRIBUTION_SOURCE_PROVENANCE_INVALID')
    plans=_load_json(required['train_formula_templates.json'])
    if not isinstance(plans,dict) or not plans:raise ValueError('GAMER_ATTRIBUTION_PLANS_MISSING')
    cases=list(_rows(required['dev_predictions.jsonl']))
    bycase={}
    for item in cases:
        if not set(CASE_FIELDS)<=set(item):raise ValueError('GAMER_ATTRIBUTION_CASE_FIELDS_MISSING')
        key=(item['case_sha256'],item['game_id'])
        if key in bycase:raise ValueError('GAMER_ATTRIBUTION_DUPLICATE_CASE')
        if item['game_id'] not in plans or item['target_zone'] not in ZONES:
            raise ValueError('GAMER_ATTRIBUTION_INVALID_CASE_PLAN_OR_ZONE')
        bycase[key]=item
    if not cases or len(cases)!=report.get('training',{}).get('dev_cases'):
        raise ValueError('GAMER_ATTRIBUTION_DEV_CASE_COUNT_MISMATCH')
    regs=defaultdict(dict)
    with required['dev_register_predictions.csv'].open('r',newline='',encoding='utf-8') as stream:
        rd=csv.DictReader(stream)
        _require_cols(rd.fieldnames,REGISTER_FIELDS,required['dev_register_predictions.csv'])
        for item in rd:
            key=(item['case_sha256'],item['game_id']);rid=item['register_id']
            if key not in bycase:raise ValueError('GAMER_ATTRIBUTION_UNREFERENCED_REGISTER')
            if rid in regs[key]:raise ValueError('GAMER_ATTRIBUTION_DUPLICATE_REGISTER')
            if item.get('split') not in ('dev',None):raise ValueError('GAMER_ATTRIBUTION_NON_DEV_REGISTER')
            regs[key][rid]={'predicted':_finite(item['prediction'],'prediction'),
                            'truth':_finite(item['target'],'oracle register target')}
    if sum(map(len,regs.values()))!=report.get('training',{}).get('dev_register_labels'):
        raise ValueError('GAMER_ATTRIBUTION_REGISTER_LABEL_COUNT_MISMATCH')
    if set(regs)!=set(bycase):raise ValueError('GAMER_ATTRIBUTION_CASE_REGISTER_MISMATCH')
    for key,case in bycase.items():
        required_names=set(plans[case['game_id']]['plan']['required_registers'])
        if set(regs[key])!=required_names:
            raise ValueError('GAMER_ATTRIBUTION_REGISTER_SCHEMA_MISMATCH: '+case['game_id'])
    return base,report,plans,cases,regs,required,source_hashes


def _score(answer,target,zone,zero_tol):
    err=abs(float(answer)-target)
    if zone=='zero':
        return {'absolute_error':err,'sign_correct':None,'zero_exact':err<=zero_tol,
                'zero_within_0_1':err<=.1}
    return {'absolute_error':err,'sign_correct':bool(answer*target>0.),
            'zero_exact':None,'zero_within_0_1':None}


def _change(before,after,*,kind):
    if before is None or after is None:return (False,False)
    return (bool(before is False and after is True),bool(before is True and after is False))


def _execute(plan,values):
    try:
        ans,_=_eval_plan(plan,values)
        if not math.isfinite(float(ans)):return None,'GAMER_ATTRIBUTION_NONFINITE_ANSWER'
        return float(ans),None
    except (ValueError,ArithmeticError,OverflowError,KeyError) as exc:
        return None,type(exc).__name__+': '+str(exc)


def _attribution_for_case(item,registers,plans,*,zero_tol=1e-6):
    """Single, pair, and full-oracle swaps for ONE correctly routed dev case."""
    game=item['game_id'];route=item['predicted_game_id'];zone=item['target_zone']
    target=_finite(item['target'],'case answer target')
    initial={'case_sha256':item['case_sha256'],'game_id':game,'predicted_game_id':route,
             'target_zone':zone,'target':target,'routed_correctly':route==game,
             'original_predicted_answer':item['predicted_answer']}
    if route!=game:
        initial.update({'status':'ROUTE_MISMATCH_NO_REGISTER_SUBSTITUTION',
                        'baseline_error':None,'oracle_formula_answer':None})
        return initial,[],[]
    plan=plans[game]['plan'];ids=sorted(registers)
    predicted={rid:registers[rid]['predicted'] for rid in ids}
    truth={rid:registers[rid]['truth'] for rid in ids}
    answer,why=_execute(plan,predicted)
    if why:
        initial.update({'status':'BASELINE_EXECUTION_FAILED','baseline_error':None,
                        'failure':why,'oracle_formula_answer':None})
        return initial,[],[]
    original=item['predicted_answer']
    if original is None or not math.isclose(answer,_finite(original,'saved predicted answer'),
                                          rel_tol=1e-9,abs_tol=1e-8):
        raise ValueError('GAMER_ATTRIBUTION_SAVED_PREDICTION_DOES_NOT_REPLAY: '+game)
    oracle,oracle_error=_execute(plan,truth)
    if oracle_error or not math.isclose(oracle,target,rel_tol=1e-9,abs_tol=1e-8):
        raise ValueError('GAMER_ATTRIBUTION_ORACLE_EXECUTION_MISMATCH: '+game)
    base=_score(answer,target,zone,zero_tol)
    initial.update({'status':'ATTRIBUTED','baseline_answer':answer,
                    'baseline_error':base['absolute_error'],'oracle_formula_answer':oracle,
                    'baseline_sign_correct':base['sign_correct'],
                    'baseline_zero_exact':base['zero_exact'],
                    'baseline_zero_within_0_1':base['zero_within_0_1']})
    singles=[];pairs=[];single_by_name={}
    for rid in ids:
        trial=dict(predicted);trial[rid]=truth[rid]
        alt,issue=_execute(plan,trial)
        row={'case_sha256':item['case_sha256'],'game_id':game,'target_zone':zone,
             'register_id':rid,'regressed_value':predicted[rid],
             'oracle_value':truth[rid],'register_absolute_error':abs(predicted[rid]-truth[rid]),
             'baseline_error':base['absolute_error'],'oracle_substituted_answer':alt,
             'execution_failure':issue}
        if issue is None:
            score=_score(alt,target,zone,zero_tol)
            row.update({'substituted_error':score['absolute_error'],
                        'absolute_error_improvement':base['absolute_error']-score['absolute_error'],
                        'sign_repaired':_change(base['sign_correct'],score['sign_correct'],kind='sign')[0],
                        'sign_damaged':_change(base['sign_correct'],score['sign_correct'],kind='sign')[1],
                        'zero_exact_repaired':_change(base['zero_exact'],score['zero_exact'],kind='zero')[0],
                        'zero_exact_damaged':_change(base['zero_exact'],score['zero_exact'],kind='zero')[1],
                        'zero_within_0_1_repaired':_change(base['zero_within_0_1'],score['zero_within_0_1'],kind='zero')[0],
                        'zero_within_0_1_damaged':_change(base['zero_within_0_1'],score['zero_within_0_1'],kind='zero')[1]})
        else:
            row.update({'substituted_error':None,'absolute_error_improvement':None,
                        'sign_repaired':False,'sign_damaged':False,'zero_exact_repaired':False,
                        'zero_exact_damaged':False,'zero_within_0_1_repaired':False,
                        'zero_within_0_1_damaged':False})
        singles.append(row);single_by_name[rid]=row
    for a,b in combinations(ids,2):
        trial=dict(predicted);trial[a]=truth[a];trial[b]=truth[b]
        alt,issue=_execute(plan,trial)
        row={'case_sha256':item['case_sha256'],'game_id':game,'target_zone':zone,
             'register_a':a,'register_b':b,'baseline_error':base['absolute_error'],
             'oracle_pair_answer':alt,'execution_failure':issue}
        if issue is None:
            score=_score(alt,target,zone,zero_tol)
            gain=base['absolute_error']-score['absolute_error']
            ga=single_by_name[a]['absolute_error_improvement']
            gb=single_by_name[b]['absolute_error_improvement']
            row.update({'pair_error':score['absolute_error'],'pair_improvement':gain,
                        'interaction_extra_improvement':gain-ga-gb if ga is not None and gb is not None else None,
                        'sign_repaired':_change(base['sign_correct'],score['sign_correct'],kind='sign')[0],
                        'zero_exact_repaired':_change(base['zero_exact'],score['zero_exact'],kind='zero')[0],
                        'zero_within_0_1_repaired':_change(base['zero_within_0_1'],score['zero_within_0_1'],kind='zero')[0]})
        else:
            row.update({'pair_error':None,'pair_improvement':None,'interaction_extra_improvement':None,
                        'sign_repaired':False,'zero_exact_repaired':False,'zero_within_0_1_repaired':False})
        pairs.append(row)
    initial['single_swap_count']=len(singles)
    initial['pair_swap_count']=len(pairs)
    initial['best_single_improvement']=max((r['absolute_error_improvement'] for r in singles
                                              if r['absolute_error_improvement'] is not None),default=None)
    initial['best_pair_improvement']=max((r['pair_improvement'] for r in pairs
                                            if r['pair_improvement'] is not None),default=None)
    return initial,singles,pairs


def _accumulator(rows,group_by,metric,case_count,*,score_fields=()):
    grouped=defaultdict(list)
    for row in rows:
        grouped[group_by(row)].append(row)
    result=[]
    for key,group in grouped.items():
        ok=[r for r in group if r[metric] is not None]
        gains=[float(r[metric]) for r in ok]
        delta=sum(gains)
        record={'key':key,'observations':len(group),'valid_interventions':len(ok),
                'invalid_interventions':len(group)-len(ok),
                'net_absolute_error_recovery':delta,
                'net_mae_reduction_over_attributed_cases':delta/case_count,
                'mean_conditional_mae_reduction':delta/len(ok) if ok else None,
                'positive_recovery_total':sum(max(v,0.) for v in gains),
                'negative_recovery_total':sum(min(v,0.) for v in gains),
                'beneficial_cases':sum(v>1e-12 for v in gains),
                'harmful_cases':sum(v < -1e-12 for v in gains)}
        if all('register_absolute_error' in r for r in group):
            record['mean_register_raw_mae']=sum(float(r['register_absolute_error']) for r in group)/len(group)
            record['worst_register_raw_error']=max(float(r['register_absolute_error']) for r in group)
        for field in score_fields:
            record[field]=sum(bool(r.get(field)) for r in ok)
        result.append(record)
    return sorted(result,key=lambda r:(-r['net_mae_reduction_over_attributed_cases'],str(r['key'])))


def _write_csv(path,rows,fields):
    with Path(path).open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)


def _zone_summary(cases):
    summary={}
    for zone in ZONES:
        subset=[c for c in cases if c['target_zone']==zone]
        valid=[c for c in subset if c['status']=='ATTRIBUTED']
        row={'cases':len(subset),'attributed_cases':len(valid),
             'baseline_raw_mae':sum(c['baseline_error'] for c in valid)/len(valid) if valid else None}
        if zone=='zero':
            row.update({'baseline_exact_zero_within_tolerance':sum(c['baseline_zero_exact'] for c in valid)/len(valid) if valid else None,
                        'baseline_zero_within_0_1':sum(c['baseline_zero_within_0_1'] for c in valid)/len(valid) if valid else None})
        else:
            row['baseline_sign_accuracy']=sum(c['baseline_sign_correct'] for c in valid)/len(valid) if valid else None
        summary[zone]=row
    return summary


def run_attribution(flat_run,*,output=None,zero_tolerance=1e-6):
    """Read saved v2 predictions. No dataset loading, LoRA capture or training."""
    from tools.nanojev_gamer_v1_diagnostic_train import _output_path
    from tools.nanojev_gamer_v1_handoff import atomic_json
    if not math.isfinite(zero_tolerance) or zero_tolerance<=0 or zero_tolerance>.1:
        raise ValueError('GAMER_ATTRIBUTION_ZERO_TOLERANCE_INVALID')
    base,source,plans,cases,registers,inputs,fingerprints=_source(flat_run)
    dest=_output_path(base,output,suffix='attribution')
    dest.mkdir(parents=True,exist_ok=False)
    stage='evaluate'
    def emit(kind,**fields):
        with (dest/'events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'time':datetime.now(timezone.utc).isoformat(),
                                      'event':kind,**fields},sort_keys=True,allow_nan=False)+'\n')
    try:
        print('GAMER FLAT-ATTRIBUTION:',dest,flush=True)
        print('  source flat run READ ONLY:',base,flush=True)
        print('  dev-case oracle substitutions; no training or Qwen capture',flush=True)
        emit('start',source_hashes=fingerprints,cases=len(cases),zero_tolerance=zero_tolerance)
        case_rows=[];single_rows=[];pair_rows=[]
        for index,case in enumerate(cases):
            key=(case['case_sha256'],case['game_id'])
            entry,singles,pairs=_attribution_for_case(case,registers[key],plans,zero_tol=zero_tolerance)
            case_rows.append(entry);single_rows.extend(singles);pair_rows.extend(pairs)
            if (index+1)%100==0:
                emit('cases_processed',completed=index+1,total=len(cases),singles=len(single_rows),pairs=len(pair_rows))
        stage='validate'
        matched=[c for c in case_rows if c['status']=='ATTRIBUTED']
        baseline_mae=sum(c['baseline_error'] for c in matched)/len(matched) if matched else None
        # The console MAE is over valid learned-route predictions. Require that
        # replaying the stored register CSV explains exactly that evaluation.
        if len(matched)==len(cases):
            expected=source['dev']['learned_route']['metrics_on_valid_predictions']['raw_mae']
            if not math.isclose(baseline_mae,float(expected),rel_tol=1e-8,abs_tol=1e-7):
                raise ValueError('GAMER_ATTRIBUTION_BASELINE_MAE_DOES_NOT_REPLAY')
        if len(matched)==0:raise ValueError('GAMER_ATTRIBUTION_NO_ATTRIBUTABLE_CASES')
        # Source evidence is immutable throughout the diagnostic.
        for name,path in inputs.items():
            if _sha_file(path)!=fingerprints[name]:
                raise ValueError('GAMER_ATTRIBUTION_SOURCE_ARTIFACT_CHANGED_DURING_RUN')
        stage='save'
        _write_csv(dest/'case_attribution.csv',case_rows,[
            'case_sha256','game_id','predicted_game_id','target_zone','status','target',
            'original_predicted_answer','baseline_answer','baseline_error','oracle_formula_answer',
            'baseline_sign_correct','baseline_zero_exact','baseline_zero_within_0_1',
            'single_swap_count','pair_swap_count','best_single_improvement','best_pair_improvement','failure'])
        _write_csv(dest/'register_interventions.csv',single_rows,[
            'case_sha256','game_id','target_zone','register_id','regressed_value','oracle_value',
            'register_absolute_error','baseline_error','oracle_substituted_answer','substituted_error',
            'absolute_error_improvement','sign_repaired','sign_damaged','zero_exact_repaired',
            'zero_exact_damaged','zero_within_0_1_repaired','zero_within_0_1_damaged','execution_failure'])
        _write_csv(dest/'pair_interventions.csv',pair_rows,[
            'case_sha256','game_id','target_zone','register_a','register_b','baseline_error',
            'oracle_pair_answer','pair_error','pair_improvement','interaction_extra_improvement',
            'sign_repaired','zero_exact_repaired','zero_within_0_1_repaired','execution_failure'])
        n=len(matched)
        special=('sign_repaired','sign_damaged','zero_exact_repaired','zero_exact_damaged',
                 'zero_within_0_1_repaired','zero_within_0_1_damaged')
        group_regs=_accumulator(single_rows,lambda r:(r['game_id'],r['register_id']),
                                 'absolute_error_improvement',n,score_fields=special)
        roles=_accumulator(single_rows,lambda r:r['register_id'],
                            'absolute_error_improvement',n,score_fields=special)
        games=_accumulator(single_rows,lambda r:r['game_id'],
                            'absolute_error_improvement',n,score_fields=special)
        pair_joint=_accumulator(pair_rows,lambda r:(r['game_id'],r['register_a'],r['register_b']),
                                'pair_improvement',n,
                                score_fields=('sign_repaired','zero_exact_repaired','zero_within_0_1_repaired'))
        interactions=_accumulator(pair_rows,lambda r:(r['game_id'],r['register_a'],r['register_b']),
                                  'interaction_extra_improvement',n,
                                  score_fields=('sign_repaired','zero_exact_repaired','zero_within_0_1_repaired'))
        report={'schema':SCHEMA,'status':'COMPLETED_READ_ONLY_DIAGNOSTIC',
           'source_flat_run':str(base),'output':str(dest),
           'source':{'dataset_sha256':source['source']['dataset_sha256'],
                     'frozen_adapter_sha256':source['source']['frozen_adapter_sha256'],
                     'source_head_sha256':source['source']['head_checkpoint_sha256'],
                     'input_files_sha256':fingerprints},
           'coverage':{'dev_cases':len(cases),'attributed_matched_route_cases':n,
                'wrong_route_cases':sum(c['status']=='ROUTE_MISMATCH_NO_REGISTER_SUBSTITUTION' for c in case_rows),
                'baseline_execution_failure_cases':sum(c['status']=='BASELINE_EXECUTION_FAILED' for c in case_rows),
                'single_interventions':len(single_rows),'pair_interventions':len(pair_rows),
                'valid_single_interventions':sum(r['absolute_error_improvement'] is not None for r in single_rows),
                'valid_pair_interventions':sum(r['pair_improvement'] is not None for r in pair_rows)},
           'baseline':{'replayed_matched_route_mae':baseline_mae,
                'source_flat_report_learned_route_mae':source['dev']['learned_route']['metrics_on_valid_predictions']['raw_mae'],
                'saved_C0_Wide_dev_mae':source['baseline_saved_C0_Wide']['raw_mae'],
                'oracle_all_registers_result_matches_target_for_all_attributed_cases':True},
           'zero_line_tolerance':zero_tolerance,
           'zero_line':_zone_summary(case_rows),
           'register_groups_by_net_consequence':group_regs,
           'roles_by_net_consequence':roles,
           'games_by_sum_of_individual_interventions':games,
           'pairs_by_joint_mae_recovery':pair_joint,
           'pairs_by_nonadditive_interaction':interactions,
           'top_positive_individual_consequences':sorted(group_regs,key=lambda r:r['positive_recovery_total'],reverse=True)[:20],
           'top_negative_individual_consequences':sorted(group_regs,key=lambda r:r['negative_recovery_total'])[:20],
           'top_joint_pair_recoveries':pair_joint[:20],
           'top_positive_pair_synergies':sorted(interactions,key=lambda r:r['net_absolute_error_recovery'],reverse=True)[:20],
           'top_negative_pair_synergies':sorted(interactions,key=lambda r:r['net_absolute_error_recovery'])[:20],
           'worst_baseline_cases':sorted(matched,key=lambda r:-r['baseline_error'])[:25],
           'interpretation':[ 
               'Dev oracle register values are used ONLY to substitute into saved predictions for diagnosis; no model weights or routing outputs are trained or changed.',
               'Single-register marginal error recovery is baseline absolute error minus absolute error after that one register is corrected; it can be NEGATIVE when erroneous registers cancel.',
               'Paired-register extra recovery subtracts the two individual recovery values from the paired recovery; nonzero values identify formula- or absolute-error-metric nonadditivity and cancellation, NOT a unique causal decomposition.',
               'Register/group rankings use sum of per-case marginal recoveries divided by the count of attributable cases, not raw register MAE.',
               'Changing the model-selected game would require a different plan and a different set of regressor predictions. Wrong-route cases are explicitly excluded from register-substitution attribution.',
               'All-register oracle recomputation is an integrity control, not an inference or model performance result.',
               'This diagnostic uses held-out dev answers for error attribution. Subsequent targeted development using these results requires a separately untouched acceptance set.',
               'Zero-line status is measured separately; no performance improvement or competence retention is implied by attribution alone.' ],
           'artifacts':['final_report.json','case_attribution.csv','register_interventions.csv',
                        'pair_interventions.csv','events.jsonl']}
        atomic_json(dest/'final_report.json',report)
        emit('complete',attributed_cases=n,single_interventions=len(single_rows),
             pair_interventions=len(pair_rows),baseline_mae=baseline_mae)
        print('GAMER FLAT-ATTRIBUTION COMPLETE:',dest/'final_report.json',flush=True)
        print('  replayed learned-route MAE:',f'{baseline_mae:.5g}',
              'C0 comparison:',f"{report['baseline']['saved_C0_Wide_dev_mae']:.5g}",flush=True)
        print('  attributed cases:',n,'/',len(cases),'single interventions:',len(single_rows),
              'pairs:',len(pair_rows),flush=True)
        print('  most consequential registers:',[(r['key'],round(r['net_mae_reduction_over_attributed_cases'],5))
              for r in group_regs[:7]],flush=True)
        print('  exact-zero within',zero_tolerance,':',
              report['zero_line']['zero']['baseline_exact_zero_within_tolerance'],flush=True)
        print('  Source run and model weights READ ONLY; no training or promotion.',flush=True)
        return report
    except Exception as exc:
        atomic_json(dest/'failure_report.json',{'schema':SCHEMA,'status':'INCOMPLETE',
                   'stage':stage,'exception_type':type(exc).__name__,'exception':str(exc),
                   'source_flat_run_unchanged':True})
        emit('failed',stage=stage,error=str(exc))
        raise
