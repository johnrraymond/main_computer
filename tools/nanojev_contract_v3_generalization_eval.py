#!/usr/bin/env python3
"""Bounded V3 model generalization benchmark, isolated from the frozen compiler.

This evaluates *Gemma/Ollama's* first-pass contract interpretation, not a native
NanoJev neural network. Author-created references are not independent expert gold.
The bridge remains responsible for source evidence, graph audit, and compilation.
No prompt assistance, retries, remote inference, keys, RPC or deployment.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from tools import nanojev_contract_v3_live_head as bridge
from tools import nanojev_contract_v3_governance_language as language

SCHEMA = 'nanojev.contract.v3.generalization_corpus.v1'
REPORT_SCHEMA = 'nanojev.contract.v3.generalization_evaluation.v1'
DEFAULT_CORPUS = Path(__file__).resolve().parent/'fixtures'/'nanojev_v3_generalization'/'manifest.json'
ALLOWED_FAMILIES = {'paraphrase', 'structure_and_contrast', 'authority',
                    'contradiction', 'adversarial', 'omission'}
CASE_ID = re.compile(r'[a-z][a-z0-9_]{2,60}\Z')
IDENT = re.compile(r'[a-z][a-z0-9_]{2,80}\Z')

class BenchmarkError(Exception):
    pass


def sha(raw:bytes|str)->str:
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode('utf-8')).hexdigest()


def canonical(obj:Any)->str:
    return json.dumps(obj,ensure_ascii=True,sort_keys=True,separators=(',',':'))


def _flatten(spec:dict)->dict:
    out={key:value for key,value in spec.items() if key not in ('milestones','policy')}
    for milestone in spec['milestones']:
        ident=milestone['id']
        for field,value in milestone.items():
            if field!='id':out['milestones.'+ident+'.'+field]=value
    for key,value in spec['policy'].items():out['policy.'+key]=value
    return out


def _topic_matches(missing:list[str],tokens:list[str])->bool:
    """Refusal must name the affected subject, not just be an arbitrary refusal."""
    if not isinstance(missing,list):return False
    # Require one coherent model objection to identify the relevant field.
    # Unrelated objections must not satisfy the topic by string aggregation.
    return any(all(token.casefold() in str(item).casefold() for token in tokens)
               for item in missing)


def validate_corpus(path:Path)->tuple[dict,list[dict],str]:
    path=Path(path).resolve()
    if not path.is_file() or path.stat().st_size>2_000_000:
        raise BenchmarkError('Corpus missing or exceeds 2 MB')
    raw=path.read_bytes()
    try:manifest=json.loads(raw)
    except (UnicodeError,ValueError) as exc:raise BenchmarkError('Invalid corpus JSON') from exc
    if not isinstance(manifest,dict) or manifest.get('schema')!=SCHEMA or set(manifest)!={'schema','origin','mode','training_separation','cases'}:
        raise BenchmarkError('Unexpected corpus schema')
    if manifest['origin']!='ASSISTANT_AUTHORED_SYNTHESES_NOT_INDEPENDENT_GOLD':
        raise BenchmarkError('Unrecognized reference provenance')
    cases=manifest['cases']
    if not isinstance(cases,list) or not 1<=len(cases)<=100:
        raise BenchmarkError('Case count outside supported bounds')
    ids=set(); pairs=defaultdict(list)
    for case in cases:
        if not isinstance(case,dict) or set(case)!={'id','family','expected','request','gold','replay','expected_refusal_topic','pairing','pair_type','changed_path'}:
            raise BenchmarkError('Case missing or extra attributes')
        cid=case['id']
        if not isinstance(cid,str) or not CASE_ID.fullmatch(cid) or cid in ids:
            raise BenchmarkError('Duplicate or unsafe case ID')
        ids.add(cid)
        if case['family'] not in ALLOWED_FAMILIES or case['expected'] not in ('contract','needs_clarification'):
            raise BenchmarkError(cid+': unsupported family or expectation')
        if not isinstance(case['request'],str) or not case['request'].strip() or len(case['request'].encode('utf-8'))>bridge.MAX_REQUEST_BYTES:
            raise BenchmarkError(cid+': invalid request size')
        if not all(token in case['request'].casefold() for token in ('escrow','payer','worker','arbitrat')):
            raise BenchmarkError(cid+': missing bridge-required role vocabulary')
        replay=case['replay']
        if not isinstance(replay,dict):raise BenchmarkError(cid+': missing replay fixture')
        if case['expected']=='contract':
            if case['expected_refusal_topic'] is not None:
                raise BenchmarkError(cid+': positive case has refusal expectation')
            try:
                gold=language.validate(case['gold'])
                interpreted,missing=bridge.interpret(case['request'],canonical(replay))
            except Exception as exc:
                raise BenchmarkError(cid+': reference or replay fails source-evidence validation: '+str(exc)) from exc
            if missing or interpreted!=gold:
                raise BenchmarkError(cid+': replay and gold disagree')
        else:
            if case['gold'] is not None or replay.get('status')!='needs_clarification':
                raise BenchmarkError(cid+': refusal case must not carry a gold contract')
            tokens=case['expected_refusal_topic']
            if not isinstance(tokens,list) or not 1<=len(tokens)<=5 or not all(isinstance(t,str) and 2<=len(t)<=50 for t in tokens):
                raise BenchmarkError(cid+': missing target-specific refusal criteria')
            if not _topic_matches(replay.get('missing'),tokens):
                raise BenchmarkError(cid+': replay refusal does not identify expected defect')
        group=case['pairing']
        if group is not None:
            if not isinstance(group,str) or not IDENT.fullmatch(group) or case['expected']!='contract':
                raise BenchmarkError(cid+': invalid paired positive test')
            if case['pair_type'] not in ('invariant','contrast'):
                raise BenchmarkError(cid+': missing pair relation')
            pairs[group].append(case)
        elif case['pair_type'] is not None or case['changed_path'] is not None:
            raise BenchmarkError(cid+': pair annotations without group')
    for group,members in pairs.items():
        if len(members)!=2 or len({c['pair_type'] for c in members})!=1:
            raise BenchmarkError(group+': pair must have exactly two cases of the same type')
        a,b=members
        same=a['gold']==b['gold']
        if a['pair_type']=='invariant':
            if not same or a['changed_path'] is not None or b['changed_path'] is not None or a['request']==b['request']:
                raise BenchmarkError(group+': invalid invariance pair')
        else:
            actual={k for k in _flatten(a['gold'])|_flatten(b['gold']) if _flatten(a['gold']).get(k)!=_flatten(b['gold']).get(k)}
            declared=set((a['changed_path'] or '').split('|'))
            if same or not actual or actual!=declared or a['changed_path']!=b['changed_path']:
                raise BenchmarkError(group+': contrast annotation differs from gold changes')
    return manifest,cases,sha(raw)


def select_cases(cases:list[dict], *, shard_index:int=0,shard_count:int=1,
                 selected_ids:list[str]|None=None)->list[dict]:
    if not 1<=shard_count<=100 or not 0<=shard_index<shard_count:
        raise BenchmarkError('Invalid shard selection')
    id_filter=set(selected_ids) if selected_ids else None
    if id_filter is not None:
        missing=id_filter-{c['id'] for c in cases}
        if missing:raise BenchmarkError('Unknown case IDs: '+', '.join(sorted(missing)))
    # Pairs are atomic: a paraphrase or contrast never spans two shards.
    bundles={}
    for case in cases:
        key='pair:'+case['pairing'] if case['pairing'] else 'case:'+case['id']
        if key not in bundles:bundles[key]=len(bundles)
    result=[]
    for case in cases:
        key='pair:'+case['pairing'] if case['pairing'] else 'case:'+case['id']
        if bundles[key]%shard_count==shard_index and (id_filter is None or case['id'] in id_filter):
            result.append(case)
    return result


def classify(case:dict, result:dict)->tuple[str,bool|None]:
    """Return (first-pass outcome, correct); None means runtime unavailable."""
    status=result.get('status')
    if status in ('INFERENCE_UNAVAILABLE_NOT_SCORED','MODEL_NO_FINAL_ANSWER_NOT_SCORED'):
        return 'INFERENCE_NOT_SCORED',None
    if case['expected']=='contract':
        if status=='REFUSED_MISSING_EXPLICIT_TERMS' or str(status).startswith('MODEL_FALSE_REFUSAL'):
            return 'FALSE_REFUSAL',False
        if str(status).startswith('ASSISTED_') or result.get('model_attempts',1)!=1:
            return 'ASSISTED_NOT_FIRST_PASS',None
        if str(status).startswith('PASS_') and result.get('semantic_comparison')=='MATCH_AUTHOR_SUPPLIED_GOLD':
            return 'CORRECT_CONTRACT',True
        if result.get('semantic_comparison')=='MISMATCH_AUTHOR_SUPPLIED_GOLD':
            return 'INCORRECT_CONTRACT_IR',False
        return 'MODEL_OUTPUT_BLOCKED_OR_INVALID',False
    # For a negative, a valid clarification must name the intended defect.
    if status=='REFUSED_MISSING_EXPLICIT_TERMS':
        return ('CORRECT_TARGETED_REFUSAL',True) if _topic_matches(result.get('clarification_needed',[]),case['expected_refusal_topic']) else ('WRONG_REFUSAL_REASON',False)
    if str(status).startswith('MODEL_FALSE_REFUSAL'):
        return 'WRONG_REFUSAL_REASON',False
    if str(status).startswith(('PASS_','ASSISTED_PASS_')):
        return 'UNSAFE_ACCEPTANCE',False
    if str(status).startswith('ASSISTED_'):
        return 'ASSISTED_NOT_FIRST_PASS',None
    return 'NONREFUSAL_BLOCKED_BY_GATE',False


def _pair_outcomes(cases:list[dict],records:list[dict])->list[dict]:
    by_id={r['id']:r for r in records}
    group=defaultdict(list)
    for c in cases:
        if c['pairing']:group[c['pairing']].append(c)
    outcome=[]
    for name,members in group.items():
        if len(members)!=2:continue  # Partial ID filters never pretend pair evaluation happened.
        a,b=members;ra=by_id[a['id']];rb=by_id[b['id']]
        hashes=[ra.get('normalized_ir_sha256'),rb.get('normalized_ir_sha256')]
        measurable=all(hashes)
        correct=ra.get('correct') is True and rb.get('correct') is True
        outcome.append({'pair':name,'kind':a['pair_type'],'cases':[a['id'],b['id']],
                        'measurable':measurable,
                        'model_relation_holds':(hashes[0]==hashes[1] if a['pair_type']=='invariant' else hashes[0]!=hashes[1]) if measurable else None,
                        'both_correct_against_author_gold':correct,
                        'changed_paths':a['changed_path'].split('|') if a['changed_path'] else []})
    return outcome


def evaluate(*,corpus:Path=DEFAULT_CORPUS,output:Path,mode:str,
             model:str=bridge.DEFAULT_MODEL,endpoint:str=bridge.MODEL_ENDPOINT,
             timeout_seconds:float=600,forge:bool=False,shard_index:int=0,
             shard_count:int=1,selected_ids:list[str]|None=None,
             stop_on_unavailable:bool=True,run_bridge=None)->dict:
    if mode not in ('replay','live'):raise BenchmarkError('Mode must be replay or live')
    if not 0<timeout_seconds<=1200:raise BenchmarkError('Invalid model timeout')
    output=Path(output).resolve()
    if output.exists():raise BenchmarkError('Refusing to overwrite an existing report')
    manifest,cases,source_hash=validate_corpus(corpus)
    selection=select_cases(cases,shard_index=shard_index,shard_count=shard_count,selected_ids=selected_ids)
    if not selection:raise BenchmarkError('Selected shard contains no cases')
    # Source fixtures, reference gold and replay answers are never included in
    # the live Ollama payload: the bridge reads ONLY each request file.
    execute=run_bridge or bridge.execute
    child_base=output.parent/(output.stem+'.cases')
    if child_base.exists():raise BenchmarkError('Refusing to reuse an existing case workspace')
    records=[];stop=False
    for case in selection:
        cid=case['id']; child_dir=child_base/cid
        if stop:
            records.append({'id':cid,'family':case['family'],'expected':case['expected'],
                            'outcome':'SKIPPED_AFTER_UNAVAILABLE','correct':None,
                            'model_invoked':False,'model_request_attempted':False,
                            'normalized_ir_sha256':None,'bridge_status':'NOT_RUN',
                            'case_report':None})
            continue
        child_dir.mkdir(parents=True,exist_ok=False)
        source=child_dir/'request.txt'
        source.write_text(case['request'],encoding='utf-8')
        golden=None
        if case['gold'] is not None:
            golden=child_dir/'reference.json'
            golden.write_text(json.dumps(case['gold'],indent=2)+'\n',encoding='utf-8')
        answer=None
        if mode=='replay':
            answer=child_dir/'authored-replay.json'
            answer.write_text(json.dumps(case['replay'],indent=2)+'\n',encoding='utf-8')
        child_report=child_dir/'bridge-report.json'
        result=execute(request=source,output=child_report,live_ollama=(mode=='live'),
                       response_file=answer, gold=golden,model=model,endpoint=endpoint,
                       timeout=timeout_seconds,forge=forge,retry_false_clarification=False)
        if result.get('model_attempts',1)>1:
            raise BenchmarkError('Bridge performed an assisted retry: refusing to count')
        outcome,correct=classify(case,result)
        # An injected transport or replay response cannot be evidence of live
        # inference even if the returned typed IR matches a reference.
        if mode=='live' and (result.get('mode')!='LIVE_OLLAMA' or not result.get('llm_inference_performed')):
            outcome,correct='INFERENCE_NOT_VERIFIED_NOT_SCORED',None
        if correct is True and result.get('evidence_alignments'):
            outcome='CORRECT_IR_WITH_EVIDENCE_ALIGNMENT'
        records.append({'id':cid,'family':case['family'],'expected':case['expected'],
                        'outcome':outcome,'correct':correct,
                        'bridge_status':result.get('status'),
                        'model_invoked':result.get('llm_inference_performed',False),
                        'model_request_attempted':result.get('model_request_attempted',False),
                        'normalized_ir_sha256':result.get('normalized_ir_sha256'),
                        'semantic_comparison':result.get('semantic_comparison'),
                        'evidence_alignments':len(result.get('evidence_alignments',[])),
                        'blocker_stages':[b.get('stage') for b in result.get('blockers',[])],
                        'elapsed_seconds':result.get('elapsed_seconds'),
                        'case_report':str(child_report),
                        'request_sha256':sha(case['request']),
                        'gold_sha256':sha(canonical(case['gold'])) if case['gold'] is not None else None,
                        'reason_target_matched':_topic_matches(result.get('clarification_needed',[]),case['expected_refusal_topic']) if case['expected_refusal_topic'] else None})
        if outcome in ('INFERENCE_NOT_SCORED','INFERENCE_NOT_VERIFIED_NOT_SCORED') and stop_on_unavailable:stop=True
    correct=sum(r['correct'] is True for r in records)
    evaluated=sum(r['correct'] is not None for r in records)
    positives=[r for r in records if r['expected']=='contract']
    negatives=[r for r in records if r['expected']=='needs_clarification']
    family={}
    for name in sorted({r['family'] for r in records}):
        sub=[r for r in records if r['family']==name]
        num=sum(r['correct'] is not None for r in sub)
        family[name]={'correct':sum(r['correct'] is True for r in sub),
                      'evaluated':num,'selected':len(sub),
                      'accuracy':sum(r['correct'] is True for r in sub)/num if num else None,
                      'outcomes':dict(Counter(r['outcome'] for r in sub))}
    pairs=_pair_outcomes(selection,records)
    complete=all(r['correct'] is not None for r in records)
    fully_scoped=(len(selection)==len(cases))
    summary={'schema':REPORT_SCHEMA,
             'status':('MEASURED_LIVE_FULL_CORPUS_NOT_CERTIFIED' if mode=='live' and complete and fully_scoped else
                       'MEASURED_LIVE_PARTIAL_SHARD_NOT_CERTIFIED' if mode=='live' and complete else
                       'REPLAY_HARNESS_FULL_CORPUS_NOT_MODEL_EVIDENCE' if mode=='replay' and complete and fully_scoped else
                       'REPLAY_HARNESS_PARTIAL_NOT_MODEL_EVIDENCE' if mode=='replay' and complete else
                       'INCOMPLETE_UNAVAILABLE_NO_ACCURACY_CLAIM'),
             'mode':mode,'model':model if mode=='live' else 'AUTHORED_REPLAY_NOT_MODEL',
             'reference_provenance':manifest['origin'],
             'native_nanojev_model_used':False,
             'independent_expert_gold':False,'training_or_improvement_evidence':False,
             'no_assisted_retries':True,'forge_requested':forge,
             'corpus_sha256':source_hash,'corpus_size':len(cases),
             'shard':{'index':shard_index,'count':shard_count,'selected':len(selection),
                      'full_corpus':fully_scoped},
             'score':{'correct':correct,'evaluated':evaluated,'selected':len(records),
                      'correct_positive':sum(r['correct'] is True for r in positives),
                      'unassisted_exact_positive':sum(r['outcome']=='CORRECT_CONTRACT' for r in positives),
                      'evidence_aligned_correct_positive':sum(r['outcome']=='CORRECT_IR_WITH_EVIDENCE_ALIGNMENT' for r in positives),
                      'evaluated_positive':sum(r['correct'] is not None for r in positives),
                      'correct_refusals':sum(r['correct'] is True for r in negatives),
                      'evaluated_refusals':sum(r['correct'] is not None for r in negatives),
                      'false_refusals':sum(r['outcome']=='FALSE_REFUSAL' for r in records),
                      'unsafe_acceptances':sum(r['outcome']=='UNSAFE_ACCEPTANCE' for r in records),
                      'incorrect_contract_ir':sum(r['outcome']=='INCORRECT_CONTRACT_IR' for r in records),
                      'wrong_reason_refusals':sum(r['outcome']=='WRONG_REFUSAL_REASON' for r in records),
                      'blocked_nonrefusals':sum(r['outcome']=='NONREFUSAL_BLOCKED_BY_GATE' for r in records),
                      'unavailable':sum(r['outcome'] in ('INFERENCE_NOT_SCORED','INFERENCE_NOT_VERIFIED_NOT_SCORED') for r in records),
                      'skipped':sum(r['outcome']=='SKIPPED_AFTER_UNAVAILABLE' for r in records),
                      'accuracy':correct/evaluated if evaluated else None},
             'by_family':family,'pair_tests':pairs,'cases':records}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return summary


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus',type=Path,default=DEFAULT_CORPUS)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--mode',choices=('replay','live'),required=True)
    p.add_argument('--model',default=bridge.DEFAULT_MODEL)
    p.add_argument('--ollama-endpoint',default=bridge.MODEL_ENDPOINT)
    p.add_argument('--timeout-seconds',type=float,default=600)
    p.add_argument('--forge',action='store_true',help='Optional expensive downstream test per accepted contract; NOT necessary to score language ability')
    p.add_argument('--shard-count',type=int,default=1)
    p.add_argument('--shard-index',type=int,default=0)
    p.add_argument('--case',action='append',help='Select an exact case ID; may be specified more than once')
    p.add_argument('--continue-after-unavailable',action='store_true')
    args=p.parse_args(argv)
    try:
        report=evaluate(corpus=args.corpus,output=args.output,mode=args.mode,
                        model=args.model,endpoint=args.ollama_endpoint,
                        timeout_seconds=args.timeout_seconds,forge=args.forge,
                        shard_index=args.shard_index,shard_count=args.shard_count,
                        selected_ids=args.case,stop_on_unavailable=not args.continue_after_unavailable)
    except (BenchmarkError,OSError,ValueError) as exc:
        print(json.dumps({'status':'FAIL_GENERALIZATION_EVAL_PREFLIGHT','reason':str(exc)}))
        return 2
    print(json.dumps({'status':report['status'],'score':report['score'],
                      'shard':report['shard'],'output':str(args.output)},separators=(',',':')))
    return 0 if report['score']['evaluated']==report['score']['selected'] else 1

if __name__=='__main__':
    raise SystemExit(main())
