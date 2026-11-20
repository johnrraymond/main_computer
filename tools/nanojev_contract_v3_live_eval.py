#!/usr/bin/env python3
"""Small held-out evaluation of the V3 language bridge.

Model runs are opt-in. Offline replay validates the pathway, NOT intelligence.
All reference specifications are author-authored, NOT independent expert gold.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
from tools import nanojev_contract_v3_live_head as bridge


class EvalError(Exception):pass


def _corpus_file(base:Path,name:str)->Path:
    if (not isinstance(name,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,100}',name)
        or '/' in name or '\\' in name):
        raise EvalError('Invalid corpus relative filename')
    path=(base/name).resolve()
    if path.parent!=base.resolve() or not path.is_file():
        raise EvalError('Missing corpus file: '+name)
    return path


def evaluate(*,manifest:Path,output:Path,mode:str,model:str=bridge.DEFAULT_MODEL,
             endpoint:str=bridge.MODEL_ENDPOINT,forge:bool=False,post=None,
             timeout_seconds:float=600,continue_after_unavailable:bool=False):
    output=Path(output).resolve()
    if output.exists():raise EvalError('Refusing to overwrite existing evaluation')
    if mode not in ('live','replay'):raise EvalError('Mode must be live or replay')
    if post is not None and mode!='live':raise EvalError('Injected transport requires live mode')
    if not 0 < timeout_seconds <= 1200:raise EvalError('Timeout must be between 0 and 1200 seconds')
    manifest=Path(manifest).resolve()
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('schema')!='nanojev.contract.v3.live_head_eval_manifest.v1':
        raise EvalError('Wrong corpus schema')
    cases=data.get('cases')
    if not isinstance(cases,list) or not cases or len(cases)>40:
        raise EvalError('Empty or oversized case inventory')
    if len({c.get('id') for c in cases})!=len(cases):
        raise EvalError('Duplicate case IDs')
    suite=[]; records=[]
    for c in cases:
        name=c.get('id')
        if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_]{2,36}',name):
            raise EvalError('Invalid case ID')
        expected=c.get('expected')
        if expected not in ('contract','needs_clarification'):
            raise EvalError('Unknown expected case result')
        req=_corpus_file(manifest.parent,c.get('request'))
        answer=_corpus_file(manifest.parent,c.get('replay'))
        gold=_corpus_file(manifest.parent,c['gold']) if expected=='contract' else None
        suite.append((name,expected,req,answer,gold))
    unavailable_seen=False
    for name,expected,request,response,gold in suite:
        if unavailable_seen and not continue_after_unavailable:
            records.append({'id':name,'expected':expected,
                            'result':'SKIPPED_AFTER_INFERENCE_UNAVAILABLE',
                            'passed':False,'evaluated':False,'model_invoked':False,
                            'model_request_attempted':False,
                            'semantic_comparison':'NOT_CHECKED',
                            'report':None,'blockers':[]})
            continue
        child=output.parent/(output.stem+'.eval-cases')/(name+'.json')
        result=bridge.execute(request=request,output=child,live_ollama=(mode=='live'),
                              response_file=response if mode=='replay' else None,
                              gold=gold,model=model,endpoint=endpoint,forge=forge,post=post,
                              timeout=timeout_seconds)
        passed=(result['status'].startswith('PASS_') and
                result['semantic_comparison']=='MATCH_AUTHOR_SUPPLIED_GOLD') if expected=='contract' else (
                result['status']=='REFUSED_MISSING_EXPLICIT_TERMS')
        unavailable=result['status']=='INFERENCE_UNAVAILABLE_NOT_SCORED'
        no_final=result['status']=='MODEL_NO_FINAL_ANSWER_NOT_SCORED'
        unavailable_seen |= (unavailable or no_final)
        evaluated=not (unavailable or no_final)
        records.append({'id':name,'expected':expected,'result':result['status'],
                        'passed':passed,'evaluated':evaluated,
                        'model_invoked':result['llm_inference_performed'],
                        'model_request_attempted':result.get('model_request_attempted',False),
                        'elapsed_seconds':result.get('elapsed_seconds'),
                        'ollama_diagnostics':result.get('ollama_diagnostics'),
                        'semantic_comparison':result['semantic_comparison'],
                        'report':str(child),'blockers':result['blockers']})
    scored=sum(r['passed'] for r in records)
    unavailable_count=sum(r['result']=='INFERENCE_UNAVAILABLE_NOT_SCORED' for r in records)
    no_final_count=sum(r['result']=='MODEL_NO_FINAL_ANSWER_NOT_SCORED' for r in records)
    skipped_count=sum(r['result']=='SKIPPED_AFTER_INFERENCE_UNAVAILABLE' for r in records)
    evaluated_count=sum(r['evaluated'] for r in records)
    positive=[r for r in records if r['expected']=='contract']
    negative=[r for r in records if r['expected']=='needs_clarification']
    report={'schema':'nanojev.contract.v3.live_head_eval_result.v1',
            'status':('INCOMPLETE_LIVE_EVAL_INFERENCE_UNAVAILABLE' if unavailable_count else
                      'INCOMPLETE_LIVE_EVAL_NO_FINAL_MODEL_ANSWER' if no_final_count else
                      ('PASS_LIVE_HELDOUT_NOT_EXPERT_CERTIFIED' if mode=='live' else
                       'PASS_OFFLINE_EVAL_PIPELINE_NOT_MODEL_VALIDATION')
                      if scored==len(records) else 'FAIL_HELDOUT_EVALUATION'),
            'mode':mode,'corpus_authorship':data.get('provenance'),
            'model':model if mode=='live' else 'REPLAY_NO_INFERENCE',
            'score':{'passed':scored,'total':len(records),
                     'evaluated':evaluated_count,'unavailable':unavailable_count,
                     'no_final_model_answer':no_final_count,
                     'skipped':skipped_count,
                     'positive_exact':sum(r['passed'] for r in positive),
                     'positive_total':len(positive),
                     'correct_refusals':sum(r['passed'] for r in negative),
                     'refusal_total':len(negative)},
            'semantic_accuracy':None if evaluated_count==0 else scored/evaluated_count,
            'model_timeout_seconds':timeout_seconds if mode=='live' else None,
            'expert_independent_gold':False,
            'model_inference_proven':bool(records) and all(r['model_invoked'] for r in records),
            'not_training_evidence':True,'cases':records}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=Path(__file__).resolve().parent/'fixtures'/'nanojev_v3_live_head'/'manifest.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--mode',choices=['live','replay'],required=True)
    p.add_argument('--model',default=bridge.DEFAULT_MODEL)
    p.add_argument('--ollama-endpoint',default=bridge.MODEL_ENDPOINT)
    p.add_argument('--forge',action='store_true')
    p.add_argument('--timeout-seconds',type=float,default=600,
                   help='Per-request Ollama response timeout (max 1200 seconds)')
    p.add_argument('--continue-after-unavailable',action='store_true',
                   help='Continue trying other cases after transport failure or model returned no answer')
    args=p.parse_args(argv)
    try:
        result=evaluate(manifest=args.manifest,output=args.output,mode=args.mode,
                        model=args.model,endpoint=args.ollama_endpoint,forge=args.forge,
                        timeout_seconds=args.timeout_seconds,
                        continue_after_unavailable=args.continue_after_unavailable)
        print(json.dumps({'status':result['status'],'score':result['score'],'output':str(args.output)}))
        return 0 if result['status'].startswith('PASS_') else 1
    except (EvalError,ValueError,OSError) as exc:
        print(json.dumps({'status':'FAIL_EVAL_PREFLIGHT','reason':str(exc)}))
        return 1

if __name__=='__main__':raise SystemExit(main())
