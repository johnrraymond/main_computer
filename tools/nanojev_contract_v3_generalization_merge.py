#!/usr/bin/env python3
"""Merge all disjoint V3 generalization shards into one measured report.

Does not infer missing results, certify semantics, or invoke any model.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from tools import nanojev_contract_v3_generalization_eval as ev


class MergeError(ev.BenchmarkError):
    pass


def merge(*,report_dir:Path,output:Path,corpus:Path=ev.DEFAULT_CORPUS)->dict:
    report_dir=Path(report_dir).resolve();output=Path(output).resolve()
    if output.exists():raise MergeError('Refusing to overwrite merged report')
    if not report_dir.is_dir():raise MergeError('Missing shard-report directory')
    manifest,cases,source_sha=ev.validate_corpus(corpus)
    files=sorted(report_dir.glob('shard-*.json'))
    if not files:raise MergeError('No shard-*.json reports found')
    reports=[]
    for p in files:
        if not p.is_file() or p.stat().st_size>3_000_000:
            raise MergeError('Invalid shard report size: '+p.name)
        data=json.loads(p.read_text(encoding='utf-8'))
        if data.get('schema')!=ev.REPORT_SCHEMA or data.get('corpus_sha256')!=source_sha:
            raise MergeError('Corpus mismatch or invalid report: '+p.name)
        reports.append((p,data))
    shard_count=reports[0][1]['shard']['count']
    if len(reports)!=shard_count:
        raise MergeError('Expected '+str(shard_count)+' shard files; found '+str(len(reports)))
    mode=reports[0][1]['mode'];model=reports[0][1]['model']
    if mode not in ('live','replay'):
        raise MergeError('Unsupported execution mode')
    observed_indices=set();by_id={};digests=[]
    for p,data in reports:
        index=data['shard']['index']
        if (not isinstance(index,int) or not 0<=index<shard_count or
            index in observed_indices or data['shard']['count']!=shard_count):
            raise MergeError('Duplicate/inconsistent shard index')
        observed_indices.add(index)
        if data['mode']!=mode or data['model']!=model:
            raise MergeError('Cannot merge different model or mode')
        if (data.get('independent_expert_gold') is not False or
            data.get('native_nanojev_model_used') is not False or
            data.get('no_assisted_retries') is not True):
            raise MergeError('Evidence provenance changed in a shard')
        expected=ev.select_cases(cases,shard_index=index,shard_count=shard_count)
        records=data.get('cases')
        if not isinstance(records,list) or [r.get('id') for r in records]!=[c['id'] for c in expected]:
            raise MergeError('Shard case inventory mismatch: '+p.name)
        if data['score'].get('selected')!=len(expected):
            raise MergeError('Shard case denominator mismatch')
        for record,c in zip(records,expected):
            cid=record['id']
            if cid in by_id or record.get('family')!=c['family'] or record.get('expected')!=c['expected']:
                raise MergeError('Duplicate or mismatched case in shards')
            if (record.get('request_sha256') != ev.sha(c['request']) or
                record.get('gold_sha256') != (ev.sha(ev.canonical(c['gold'])) if c['gold'] is not None else None)):
                raise MergeError('Case content-hash mismatch: '+cid)
            if mode=='live' and record.get('correct') is True and not record.get('model_invoked'):
                raise MergeError('Unverified model run marked correct')
            if record.get('correct') not in (True,False,None):
                raise MergeError('Case has invalid correctness value')
            by_id[cid]=record
        digests.append({'path':str(p),'sha256':ev.sha(p.read_bytes()),'index':index})
    if observed_indices!=set(range(shard_count)) or set(by_id)!={c['id'] for c in cases}:
        raise MergeError('Some cases or shard indices are missing')
    records=[by_id[c['id']] for c in cases]
    correct=sum(r['correct'] is True for r in records)
    evaluated=sum(r['correct'] is not None for r in records)
    positives=[r for r in records if r['expected']=='contract']
    negatives=[r for r in records if r['expected']=='needs_clarification']
    families={}
    for name in sorted(ev.ALLOWED_FAMILIES):
        sub=[r for r in records if r['family']==name]
        n=sum(r['correct'] is not None for r in sub)
        families[name]={'correct':sum(r['correct'] is True for r in sub),
                        'evaluated':n,'selected':len(sub),
                        'accuracy':sum(r['correct'] is True for r in sub)/n if n else None,
                        'outcomes':dict(Counter(r['outcome'] for r in sub))}
    pairs=ev._pair_outcomes(cases,records)
    complete=evaluated==len(cases)
    result={'schema':ev.REPORT_SCHEMA,
            'status':('MEASURED_LIVE_FULL_CORPUS_NOT_CERTIFIED' if complete and mode=='live' else
                      'REPLAY_HARNESS_FULL_CORPUS_NOT_MODEL_EVIDENCE' if complete else
                      'INCOMPLETE_SHARDS_NO_FULL_ACCURACY_CLAIM'),
            'mode':mode,'model':model,
            'reference_provenance':manifest['origin'],
            'native_nanojev_model_used':False,'independent_expert_gold':False,
            'training_or_improvement_evidence':False,'no_assisted_retries':True,
            'forge_requested':all(r.get('forge_requested') for _,r in reports),
            'corpus_sha256':source_sha,'corpus_size':len(cases),
            'shard':{'index':None,'count':shard_count,'selected':len(cases),'full_corpus':True,
                     'merge_complete':True},
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
            'by_family':families,'pair_tests':pairs,'merged_shards':sorted(digests,key=lambda d:d['index']),
            'cases':records}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--corpus',type=Path,default=ev.DEFAULT_CORPUS)
    args=p.parse_args(argv)
    try:result=merge(report_dir=args.report_dir,output=args.output,corpus=args.corpus)
    except (OSError,ValueError,MergeError) as exc:
        print(json.dumps({'status':'FAIL_GENERALIZATION_MERGE','reason':str(exc)}))
        return 2
    print(json.dumps({'status':result['status'],'score':result['score'],
                      'output':str(args.output)},separators=(',',':')))
    return 0 if result['score']['selected']==result['score']['evaluated'] else 1

if __name__=='__main__':raise SystemExit(main())
