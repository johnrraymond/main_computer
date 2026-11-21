#!/usr/bin/env python3
"""Controlled baseline-versus-multipass V3 model experiment on pinned fresh cases.

Tracks MODEL semantic accuracy separately from SAFETY GATE catch rate. A
rejection by deterministic source-coverage checks is NEVER counted as the model
correctly diagnosing a contract. No retraining, Forge by default, keys or RPC.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from pathlib import Path
import json

from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_generalization_eval as ge
from tools import nanojev_contract_v3_deliberative as deliberative
from tools import nanojev_contract_v3_make_fresh_challenge as challenge

PINNED_FRESH_SHA256='605b59bc88da58971957d8a07cc8b1c9d1eb45ac2ecd11cbc0a584fcedc39026'
PINNED_FORTY_SHA256='2650f67fe9a165a43d21198ee6e7941b62219d66fc915299859e40ce060b4d87'


class BakeoffError(Exception):pass


def classify_deliberative(case:dict,result:dict)->tuple[str,bool|None,bool]:
    """(outcome, correct on semantic task, safe terminal disposition)."""
    status=result.get('status','')
    if status in ('INFERENCE_UNAVAILABLE_NOT_SCORED','MODEL_NO_FINAL_ANSWER_NOT_SCORED'):
        return 'INFERENCE_NOT_SCORED',None,False
    correct_refusal_topic=ge._topic_matches(result.get('clarification_needed',[]),case['expected_refusal_topic'] or [])
    if case['expected']=='contract':
        if status.startswith('PASS_'):
            if result.get('semantic_comparison')=='MATCH_AUTHOR_SUPPLIED_GOLD':
                return 'CORRECT_MULTIPASS_CONTRACT',True,True
            return 'WRONG_MULTIPASS_CONTRACT',False,False
        if status.startswith('REFUSED_'):return 'FALSE_REFUSAL',False,True
        if any(x.get('stage')=='SEMANTIC_MISMATCH' for x in result.get('blockers',[])):
            return 'INCORRECT_CONTRACT_IR',False,False
        return 'VALID_REQUEST_BLOCKED_BY_GATE',False,True
    if status.startswith('PASS_'):
        return 'UNSAFE_ACCEPTANCE',False,False
    if status.startswith('REFUSED_'):
        if correct_refusal_topic:return 'CORRECT_TARGETED_REFUSAL',True,True
        return 'WRONG_REFUSAL_REASON',False,True
    # Even if gated, the MODEL did not correctly diagnose the contract.
    return 'GATE_BLOCKED_UNSAFE_PROPOSAL',False,True


def summarize(records:list[dict],name:str)->dict:
    sub=[r for r in records if r['strategy']==name]
    measured=[r for r in sub if r['correct'] is not None]
    pos=[r for r in measured if r['expected']=='contract']
    neg=[r for r in measured if r['expected']=='needs_clarification']
    ok=sum(r['correct'] for r in measured)
    return {'strategy':name,'measured':len(measured),'selected':len(sub),
            'correct':ok,'accuracy':ok/len(measured) if measured else None,
            'correct_positive':sum(r['correct'] for r in pos), 'total_positive':len(pos),
            'correct_refusals':sum(r['correct'] for r in neg),'total_refusals':len(neg),
            'unsafe_acceptances':sum(r['outcome']=='UNSAFE_ACCEPTANCE' for r in neg),
            'gated_negative_blocks':sum(r['outcome']=='GATE_BLOCKED_UNSAFE_PROPOSAL' for r in neg),
            'correct_negative_safe_dispositions':sum(r['safe'] for r in neg),
            'false_refusals':sum(r['outcome']=='FALSE_REFUSAL' for r in pos),
            'wrong_ir':sum(r['outcome'] in ('INCORRECT_CONTRACT_IR','WRONG_MULTIPASS_CONTRACT') for r in pos),
            'model_calls':sum(r['model_calls'] for r in sub),
            'outcomes':dict(Counter(r['outcome'] for r in sub))}


def execute(*,output:Path,corpus:Path=challenge.OUT,
            corpus_sha256:str|None=None,mode:str='live',strategy:str='both',
            model:str=bridge.DEFAULT_MODEL,review_model:str|None=None,
            independent_review_model:str|None=None,postflight_review:bool=True,timeout:float=600,
            shard_index:int=0,shard_count:int=1,selected_ids:list[str]|None=None,
            stop_on_unavailable:bool=True,
            run_baseline=None,run_deliberative=None)->dict:
    if mode!='live':raise BakeoffError('Live-only comparison; scripted replay is tested separately')
    if strategy not in ('both','baseline','deliberative'):raise BakeoffError('Invalid strategy')
    output=Path(output).resolve()
    if output.exists():raise BakeoffError('Refusing to overwrite a measured result')
    _,cases,h=ge.validate_corpus(corpus)
    expected_hash=corpus_sha256 or (PINNED_FRESH_SHA256 if Path(corpus).resolve()==challenge.OUT.resolve() else None)
    if not expected_hash or h!=expected_hash:
        raise BakeoffError('Unpinned or modified evaluation corpus')
    subset=ge.select_cases(cases,shard_index=shard_index,shard_count=shard_count,
                           selected_ids=selected_ids)
    if not subset:raise BakeoffError('Empty selection')
    root=output.parent/(output.stem+'.cases')
    if root.exists():raise BakeoffError('Existing workspace: refusing to mix runs')
    baseline=run_baseline or bridge.execute
    multipass=run_deliberative or deliberative.execute
    records=[]
    interrupted=False
    for case in subset:
        for arm in (('baseline','deliberative') if strategy=='both' else (strategy,)):
            if interrupted:
                records.append({'id':case['id'],'family':case['family'],
                                'expected':case['expected'],'strategy':arm,
                                'outcome':'NOT_RUN_AFTER_UNAVAILABLE','correct':None,
                                'safe':False,'model_calls':0})
                continue
            sub=root/case['id']/arm
            sub.mkdir(parents=True,exist_ok=False)
            request=sub/'request.txt';request.write_text(case['request'],encoding='utf-8')
            gold=None
            if case['gold'] is not None:
                gold=sub/'author-reference.json'
                gold.write_text(json.dumps(case['gold'],indent=2)+'\n',encoding='utf-8')
            report_path=sub/'result.json'
            if arm=='baseline':
                res=baseline(request=request,output=report_path,live_ollama=True,
                             gold=gold,model=model,timeout=timeout,forge=False,
                             retry_false_clarification=False)
                outcome,correct=ge.classify(case,res)
                safe=case['expected']=='needs_clarification' and outcome!='UNSAFE_ACCEPTANCE' or (
                        case['expected']=='contract' and outcome=='CORRECT_CONTRACT')
            else:
                res=multipass(request=request,output=report_path,mode='live',model=model,
                              review_model=review_model,
                              independent_review_model=independent_review_model,
                              timeout=timeout,forge=False,gold=gold,postflight_review=postflight_review)
                outcome,correct,safe=classify_deliberative(case,res)
            # Fake transports, offline replay, or missing proof must never count.
            if not res.get('llm_inference_performed') or ('mode' in res and res.get('mode') not in ('LIVE_OLLAMA','LIVE')):
                outcome='LIVE_INFERENCE_NOT_VERIFIED';correct=None;safe=False
            record={'id':case['id'],'family':case['family'],'expected':case['expected'],
                    'strategy':arm,'outcome':outcome,'correct':correct,'safe':safe,
                    'model_calls':res.get('model_calls',res.get('model_attempts',1)),
                    'status':res.get('status'),'semantic_comparison':res.get('semantic_comparison'),
                    'request_sha256':ge.sha(case['request']),
                    'report':str(report_path),
                    'refusal_topic_matched':(ge._topic_matches(res.get('clarification_needed',[]),case['expected_refusal_topic'])
                                             if case['expected_refusal_topic'] else None),
                    'blocker_stages':[b.get('stage') for b in res.get('blockers',[])]}
            records.append(record)
            if correct is None and stop_on_unavailable:
                interrupted=True
    by_arm={arm:summarize(records,arm) for arm in ('baseline','deliberative')
            if arm in (('baseline','deliberative') if strategy=='both' else (strategy,))}
    complete=all(r['correct'] is not None for r in records)
    diff=None
    if strategy=='both' and complete:
        diff={'correct_delta':by_arm['deliberative']['correct']-by_arm['baseline']['correct'],
              'unsafe_acceptance_delta':by_arm['deliberative']['unsafe_acceptances']-by_arm['baseline']['unsafe_acceptances'],
              'NOTE':'system-level multipass versus single-pass; NOT evidence of weight learning'}
    report={'schema':'nanojev.contract.v3.semantic_bakeoff.v1',
            'status':'MEASURED_LIVE_BAKEOFF_NOT_CERTIFIED' if complete else 'INCOMPLETE_LIVE_BAKEOFF_NOT_SCORED',
            'mode':'live','model':model,'review_model':review_model or model,
            'independent_review_model':independent_review_model,
            'model_weights_unchanged':True,'candidate_is_multipass_not_first_pass':True,
            'postflight_review_enabled':postflight_review,
            'native_nanojev_neural_model_used':False,'independent_gold':False,
            'baseline_40_corpus_sha256':PINNED_FORTY_SHA256,
            'fresh_corpus_sha256':h,'corpus_size':len(cases),
            'selected':len(subset),'shard':{'index':shard_index,'count':shard_count},
            'by_strategy':by_arm,'comparison':diff,'cases':records}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,default=challenge.OUT)
    p.add_argument('--corpus-sha256',default=None,help='Required when using a custom pinned corpus')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--strategy',choices=('both','baseline','deliberative'),default='both')
    p.add_argument('--model',default=bridge.DEFAULT_MODEL)
    p.add_argument('--review-model',default=None)
    p.add_argument('--independent-review-model',default=None)
    p.add_argument('--no-postflight-review',action='store_true',
                   help='Ablation: disable fourth-pass independent review')
    p.add_argument('--timeout-seconds',type=float,default=600)
    p.add_argument('--shard-index',type=int,default=0)
    p.add_argument('--shard-count',type=int,default=1)
    p.add_argument('--case',action='append')
    args=p.parse_args(argv)
    try:
        rep=execute(output=args.output,corpus=args.corpus,corpus_sha256=args.corpus_sha256,
                    strategy=args.strategy,model=args.model,review_model=args.review_model,
                    independent_review_model=args.independent_review_model,
                    postflight_review=not args.no_postflight_review,timeout=args.timeout_seconds,shard_index=args.shard_index,
                    shard_count=args.shard_count,selected_ids=args.case)
    except (BakeoffError,ge.BenchmarkError,ValueError,OSError) as exc:
        print(json.dumps({'status':'FAIL_BAKEOFF_PREFLIGHT','reason':str(exc)}))
        return 2
    print(json.dumps({'status':rep['status'],'by_strategy':rep['by_strategy'],
                      'comparison':rep['comparison'],'output':str(args.output)}))
    return 0 if rep['status'].startswith('MEASURED_') else 1

if __name__=='__main__':raise SystemExit(main())
