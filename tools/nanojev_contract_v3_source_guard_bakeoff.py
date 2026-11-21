#!/usr/bin/env python3
"""Evaluate a bounded source-evidence guard around original single-pass Gemma.

This is NOT a neural improvement experiment and not independent expert gold.
Count model semantic correctness separately from deterministic guard interventions.
The 24-case corpus was already inspected and is a *development regression* set.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from tools import nanojev_contract_v3_generalization_eval as benchmark
from tools import nanojev_contract_v3_make_fresh_challenge as challenge
from tools import nanojev_contract_v3_semantic_bakeoff as prior_bakeoff
from tools import nanojev_contract_v3_source_fidelity as fidelity
from tools import nanojev_contract_v3_guarded_single_pass as guarded
from tools import nanojev_contract_v3_live_head as bridge

PINNED_CORPUS = prior_bakeoff.PINNED_FRESH_SHA256


class GuardBakeoffError(Exception):pass


def _base_comparison(path:Path|None, source_hash:str, cases:list[dict], model:str)->dict|None:
    if path is None:return None
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if (data.get('schema')!='nanojev.contract.v3.semantic_bakeoff.v1'
        or data.get('fresh_corpus_sha256')!=source_hash or data.get('model')!=model):
        raise GuardBakeoffError('Original paired baseline does not match corpus or model')
    base=data.get('by_strategy',{}).get('baseline',{})
    if base.get('measured')!=len(cases) or base.get('selected')!=len(cases):
        raise GuardBakeoffError('Original baseline is not complete')
    records={c['id']:c for c in data.get('cases',[]) if c.get('strategy')=='baseline'}
    if len(records)!=len(cases):raise GuardBakeoffError('Baseline case coverage mismatch')
    for case in cases:
        record=records.get(case['id'])
        if not record or record.get('request_sha256')!=benchmark.sha(case['request']):
            raise GuardBakeoffError('Baseline request content mismatch for '+case['id'])
    return {'measured':base.get('measured'), 'correct':base.get('correct'),
            'valid_contracts_correct':base.get('correct_positive'),
            'correct_model_refusals':base.get('correct_refusals'),
            'unsafe_acceptances':base.get('unsafe_acceptances'),
            'model_calls':base.get('model_calls')}


def _classify(case:dict, report:dict)->tuple[str,bool|None,bool,bool]:
    """Outcome, SYSTEM correct on authored cases, safe terminal, MODEL correct.

    `MODEL correct` is never attributed to the source guard, even if its source
    observations agree with the authored label.
    """
    status=report.get('status','')
    if status in ('INFERENCE_UNAVAILABLE_NOT_SCORED','MODEL_NO_FINAL_ANSWER_NOT_SCORED'):
        return 'INFERENCE_NOT_SCORED',None,False,False
    negative=case['expected']=='needs_clarification'
    if status=='SOURCE_GUARD_BLOCKED_BEFORE_MODEL':
        if negative:return 'SOURCE_GUARD_BLOCKED_NEGATIVE',True,True,False
        return 'SOURCE_GUARD_FALSE_POSITIVE',False,True,False
    if status=='SOURCE_GUARD_BLOCKED_CANDIDATE_BEFORE_SOLIDITY':
        if negative:return 'IR_FIDELITY_GATE_BLOCKED_NEGATIVE',True,True,False
        return 'IR_FIDELITY_GATE_BLOCKED_VALID',False,True,False
    if status.startswith('PASS_'):
        if negative:return 'UNSAFE_ACCEPTANCE',False,False,False
        if report.get('semantic_comparison')=='MATCH_AUTHOR_SUPPLIED_GOLD':
            return 'CORRECT_MODEL_CONTRACT',True,True,True
        return 'WRONG_MODEL_CONTRACT',False,False,False
    if status=='MODEL_REQUESTED_CLARIFICATION':
        if not negative:return 'FALSE_MODEL_REFUSAL',False,True,False
        topical=benchmark._topic_matches(report.get('clarification_needed',[]),case['expected_refusal_topic'])
        if topical:return 'CORRECT_TARGETED_MODEL_REFUSAL',True,True,True
        return 'WRONG_MODEL_REFUSAL_REASON',False,True,False
    if not negative:return 'VALID_REQUEST_BLOCKED_NONMODEL',False,True,False
    return 'NONREFUSAL_BLOCKED_BY_EXISTING_GATE',False,True,False


def _summary(rows:list[dict])->dict:
    counted=[r for r in rows if r['correct'] is not None]
    pos=[r for r in counted if r['expected']=='contract']
    neg=[r for r in counted if r['expected']=='needs_clarification']
    return {'counted':len(counted),'total':len(rows),
            'system_correct_on_authored_cases':sum(x['correct'] for x in counted),
            'system_accuracy_on_authored_cases':sum(x['correct'] for x in counted)/len(counted) if counted else None,
            'valid_contracts_correct':sum(x['correct'] for x in pos), 'valid_contracts_total':len(pos),
            'model_correct_contracts':sum(x['outcome']=='CORRECT_MODEL_CONTRACT' for x in rows),
            'correct_targeted_model_refusals':sum(x['outcome']=='CORRECT_TARGETED_MODEL_REFUSAL' for x in rows),
            'source_guard_blocks':sum(x['outcome']=='SOURCE_GUARD_BLOCKED_NEGATIVE' for x in rows),
            'source_guard_false_positives':sum(x['outcome']=='SOURCE_GUARD_FALSE_POSITIVE' for x in rows),
            'unsafe_acceptances':sum(x['outcome']=='UNSAFE_ACCEPTANCE' for x in rows),
            'negative_safe_terminal':sum(x['safe'] for x in neg),
            'negative_total':len(neg),
            'model_calls':sum(x['model_calls'] for x in rows),
            'outcomes':dict(Counter(x['outcome'] for x in rows))}


def execute(*,output:Path,mode:str='audit',model:str=bridge.DEFAULT_MODEL,
            timeout:float=600,baseline_report:Path|None=None,
            selected_ids:list[str]|None=None,run_guarded=None,meta_update:Path|None=None)->dict:
    if mode not in ('audit','live'):raise GuardBakeoffError('Mode must be audit or live')
    output=Path(output).resolve()
    if output.exists():raise GuardBakeoffError('Refusing to overwrite a report')
    _,all_cases,h=benchmark.validate_corpus(challenge.OUT)
    if h!=PINNED_CORPUS:raise GuardBakeoffError('Pinned 24-case corpus has changed')
    selected=all_cases if not selected_ids else [c for c in all_cases if c['id'] in set(selected_ids)]
    if (not selected or (selected_ids is not None and
                         (len(selected)!=len(set(selected_ids)) or len(selected_ids)!=len(set(selected_ids))))):
        raise GuardBakeoffError('Unknown or duplicate selected case IDs')
    if baseline_report is not None and selected_ids:
        raise GuardBakeoffError('Full baseline comparison requires all 24 cases')
    base=_base_comparison(baseline_report,h,all_cases,model)
    root=output.parent/(output.stem+'.cases')
    if root.exists():raise GuardBakeoffError('Refusing existing result workspace')
    runner=run_guarded or guarded.execute
    rows=[]
    incomplete=False
    for case in selected:
        audit=fidelity.audit_source(case['request'])
        row={'id':case['id'],'family':case['family'],'expected':case['expected'],
             'source_observations':audit['recognized_values'],'source_unparsed_lines':len(audit['unparsed_lines']),
             'source_issues':audit['issues'],'request_sha256':benchmark.sha(case['request'])}
        if mode=='audit':
            row.update({'outcome':('SOURCE_GUARD_POTENTIAL_BLOCK' if audit['issues'] else 'SOURCE_GUARD_PASS_TO_MODEL'),
                        'model_calls':0,'correct':None,'safe':bool(audit['issues'])})
        else:
            sub=root/case['id'];sub.mkdir(parents=True,exist_ok=False)
            request=sub/'request.txt';request.write_text(case['request'],encoding='utf-8')
            gold=None
            if case['gold'] is not None:
                gold=sub/'author-reference.json';gold.write_text(json.dumps(case['gold'],indent=2)+'\n',encoding='utf-8')
            kwargs={'meta_update':meta_update} if meta_update is not None else {}
            res=runner(request=request,output=sub/'result.json',live_ollama=True,
                       model=model,timeout=timeout,forge=False,gold=gold,**kwargs)
            label,correct,safe,model_correct=_classify(case,res)
            if res.get('model_calls',0)>0 and (not res.get('llm_inference_performed') or res.get('mode')!='LIVE_OLLAMA'):
                label='MODEL_INFERENCE_NOT_VERIFIED';correct=None;safe=False;model_correct=False
            row.update({'outcome':label,'correct':correct,'safe':safe,
                        'model_correct':model_correct,'model_calls':res.get('model_calls',0),
                        'status':res.get('status'), 'report':str(sub/'result.json'),
                        'semantic_comparison':res.get('semantic_comparison')})
            if correct is None:incomplete=True
        rows.append(row)
        print(json.dumps({'case':row['id'],'outcome':row['outcome'],
                          'model_calls':row['model_calls']}),flush=True)
        if incomplete:break
    report={'schema':'nanojev.contract.v3.source_guard_bakeoff.v1',
            'status':'SOURCE_AUDIT_ONLY_NO_AI_PROOF' if mode=='audit' else
                'INCOMPLETE_LIVE_NOT_SCORED' if incomplete else 'MEASURED_LIVE_WITH_GUARD_NOT_CERTIFIED',
            'mode':mode,'model':model,'baseline_report':str(baseline_report) if baseline_report else None,
            'original_baseline':base,
            'development_corpus_already_EXPOSED':True,
            'reference_provenance':'ASSISTANT_AUTHORED_NOT_EXPERT_GOLD',
            'model_weights_unchanged':True,'frozen_kernel_unchanged':True,
            'meta_update_file':str(meta_update) if meta_update else None,
            'source_guard_is_not_model_intelligence':True,
            'corpus_sha256':h,'selected':len(selected),'cases':rows,
            'summary':_summary(rows) if mode=='live' else {
                'potential_negative_blocks':sum(r['expected']=='needs_clarification' and r['source_issues']!=[] for r in rows),
                'potential_valid_overblocks':sum(r['expected']=='contract' and r['source_issues']!=[] for r in rows),
                'unresolved_negative_requests':sum(r['expected']=='needs_clarification' and not r['source_issues'] for r in rows),
                'model_calls':0}}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['audit','live'],default='audit')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline-report',type=Path)
    p.add_argument('--model',default=bridge.DEFAULT_MODEL)
    p.add_argument('--meta-update',type=Path,help='Opt-in locally promoted meta prompt')
    p.add_argument('--timeout-seconds',type=float,default=600)
    p.add_argument('--case',action='append')
    args=p.parse_args(argv)
    try: r=execute(output=args.output,mode=args.mode,baseline_report=args.baseline_report,
                   model=args.model,timeout=args.timeout_seconds,selected_ids=args.case,meta_update=args.meta_update)
    except (GuardBakeoffError,benchmark.BenchmarkError,ValueError,OSError,KeyError) as e:
        print(json.dumps({'status':'FAIL_PREFLIGHT','reason':str(e)}));return 2
    print(json.dumps({'status':r['status'],'summary':r['summary'],'output':str(args.output)}))
    return 0 if r['status']!='INCOMPLETE_LIVE_NOT_SCORED' else 1

if __name__=='__main__':raise SystemExit(main())
