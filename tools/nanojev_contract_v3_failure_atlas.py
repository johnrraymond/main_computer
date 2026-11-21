#!/usr/bin/env python3
"""Explain the frozen 40-case V3 live baseline without modifying it.

Only run-record facts are reported as facts. Motives such as 'template anchoring'
are hypotheses; raw candidate IR was not retained in the baseline report.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path

from tools import nanojev_contract_v3_generalization_eval as ge
from tools import nanojev_contract_v3_semantic_bakeoff as bakeoff


class AtlasError(Exception):pass


def build(report:dict, corpus:Path)->dict:
    if report.get('schema')!='nanojev.contract.v3.generalization_evaluation.v1' or report.get('status')!='MEASURED_LIVE_FULL_CORPUS_NOT_CERTIFIED':
        raise AtlasError('Expected completed measured live baseline')
    _,cases,h=ge.validate_corpus(corpus)
    if h!=bakeoff.PINNED_FORTY_SHA256 or report.get('corpus_sha256')!=h:
        raise AtlasError('Refusing to interpret a changed benchmark')
    record=report.get('cases',[])
    if len(record)!=40 or len({r['id'] for r in record})!=40 or {r['id'] for r in record}!={c['id'] for c in cases}:
        raise AtlasError('Incomplete or inconsistent baseline cases')
    original=next(r for r in record if r['id']=='para_1_v0')
    original_ir=original.get('normalized_ir_sha256')
    if not original_ir:raise AtlasError('Original baseline IR unavailable')
    offending=[]
    for item in record:
        if item['correct'] is True:continue
        match=next(c for c in cases if c['id']==item['id'])
        offending.append({'id':item['id'],'family':item['family'],'outcome':item['outcome'],
           'bridge_status':item.get('bridge_status'),'original_ir_reused':(
               item.get('normalized_ir_sha256')==original_ir if item.get('normalized_ir_sha256') else None),
           'source_sha256':item.get('request_sha256'),
           'expected_refusal_topic':match['expected_refusal_topic'],
           'case_report':item.get('case_report')})
    unsafe=[x for x in offending if x['outcome']=='UNSAFE_ACCEPTANCE']
    wrong_ir=[x for x in offending if x['outcome']=='INCORRECT_CONTRACT_IR']
    refusal=[x for x in offending if x['outcome']=='WRONG_REFUSAL_REASON']
    by_family={f:Counter(x['outcome'] for x in offending if x['family']==f) for f in sorted({c['family'] for c in cases})}
    return {'schema':'nanojev.contract.v3.failure_atlas.v1','status':'DESCRIPTIVE_BASELINE_NO_NEW_INFERENCE',
            'corpus_sha256':h,'baseline_correct':report['score']['correct'],
            'baseline_total':report['score']['evaluated'],
            'unsafe_acceptance_count':len(unsafe),
            'unsafe_reusing_original_ir':sum(x['original_ir_reused'] is True for x in unsafe),
            'wrong_ir_count':len(wrong_ir),
            'wrong_refusal_reason_count':len(refusal),
            'unsafe_cases':[x['id'] for x in unsafe],
            'wrong_ir_cases':[x['id'] for x in wrong_ir],
            'wrong_reason_cases':[x['id'] for x in refusal],
            'outcome_counts':dict(Counter(x['outcome'] for x in offending)),
            'by_family':{k:dict(v) for k,v in by_family.items()},
            'hypotheses_NOT_proven':[
                'Template anchoring is consistent with repeated identical IR hashes, not established causally',
                'Exact field-level reasons for incorrect IR require the case-level raw model response',
                'Author-supplied gold may itself contain mistakes or undisclosed ambiguities',
            ],'failures':offending}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--corpus',type=Path,default=ge.DEFAULT_CORPUS)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    if args.output.exists():raise SystemExit('Refusing to overwrite atlas')
    atlas=build(json.loads(args.baseline.read_text(encoding='utf-8')),args.corpus)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(atlas,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:atlas[key] for key in ('status','baseline_correct','baseline_total',
          'unsafe_acceptance_count','unsafe_reusing_original_ir','wrong_ir_count',
          'wrong_refusal_reason_count')}))

if __name__=='__main__':main()
