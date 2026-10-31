#!/usr/bin/env python3
"""MCEL 07B.1a: layout-blind semantic sufficiency audit, NOT interface synthesis.

Run: python -m tools.mcel_promise_wave7b1a_frob
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.semantic_graph import (
    audit_application, extract_validated_graph,
)

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT=ROOT/'runtime/reports/mcel-promise-wave7b1a/report.json'
FILE_EXPLORER=ROOT/'main_computer/web/applications/scripts/mcel-file-explorer-candidate-ir.js'
TASK_NOTEBOOK=ROOT/'main_computer/web/applications/scripts/mcel-task-manager-notebook-candidate-ir.js'
COUNTER=ROOT/'tests/fixtures/mcel_application_ir/reference-counter.ir.json'


def generated_ir(path):
    text=path.read_text(encoding='utf8')
    match=re.search(r'\bir\s*:\s*(\{)', text)
    if not match:
        raise ValueError('No JSON-literal IR in generated candidate artifact '+str(path))
    ir=json.JSONDecoder().raw_decode(text[match.start(1):])[0]
    if not isinstance(ir,dict):
        raise ValueError('Generated IR must be a JSON object')
    return ir


def source_truth(ir):
    provenance=(ir.get('provenance') or {}).get('frontend') or {}
    files=provenance.get('sourceFiles') or []
    paths=[entry.get('path') for entry in files if isinstance(entry,dict) and entry.get('path')]
    for fallback in [ir.get('application',{}).get('source',{}).get('file')]:
        if fallback and fallback not in paths:
            paths.append(fallback)
    missing=[p for p in paths if not (ROOT / p).is_file()]
    return {'status':'NOT_RUN_SOURCE_MISSING' if missing else 'AVAILABLE' if paths else 'SOURCE_UNDECLARED',
            'missing':missing, 'declared':paths}


def run(output=DEFAULT_OUTPUT):
    catalog=load_catalog()
    cases={
        'file-explorer-generated-artifact': (generated_ir(FILE_EXPLORER),True),
        'task-manager-notebook-generated-artifact': (generated_ir(TASK_NOTEBOOK),False),
        'reference-counter-fixture': (json.loads(COUNTER.read_text(encoding='utf8')),False),
    }
    result={'schema':'mcel.promise-wave7b1a-feasibility.v1',
            'generatedUtc':datetime.now(timezone.utc).isoformat(),
            'overall':'FAIL', 'claim':'semantic-dependency-audit-only; no interface synthesized',
            'runtimeChanged':False,'synthesizedInterfaces':0,
            'browserEvidence':'NOT_RUN', 'hostedEvidence':'NOT_RUN',
            'cases':{},'checks':{}}
    for name,(raw,joined) in cases.items():
        graph=audit_application(raw,catalog=catalog if joined else None)
        result['cases'][name]={'graph':graph,'sourceTruth':source_truth(raw),
           'artifactSha256':'sha256:'+hashlib.sha256(
             (FILE_EXPLORER if name.startswith('file') else TASK_NOTEBOOK if name.startswith('task') else COUNTER).read_bytes()
           ).hexdigest()}
    file_graph=result['cases']['file-explorer-generated-artifact']['graph']
    task_graph=result['cases']['task-manager-notebook-generated-artifact']['graph']
    counter_graph=result['cases']['reference-counter-fixture']['graph']
    validated=validate_application_ir(cases['file-explorer-generated-artifact'][0])
    baseline=extract_validated_graph(validated.normalized)
    poison=dict(validated.normalized)
    poison['surfaces']=[{'applicationShell':{'zone':'poison'}}]
    poison['layouts']=[{'orderedChildren':['fake','roots','entry']}]
    stable=extract_validated_graph(poison) == baseline
    checks={
      'allValidated':all(case['graph'].get('valid') for case in result['cases'].values()),
      'fileExplorerInsufficient':file_graph['status']=='SEMANTICS_INSUFFICIENT',
      'fileExplorerUnjoined':file_graph['catalogJoin']['status']=='BLOCKED_UNJOINED_SOURCES',
      'noFabricatedTaskCoordination':all(case['graph']['counts']['verifiedTaskCoordinations']==0 for case in result['cases'].values()),
      'taskManagerHasRealDependencies':task_graph['counts']['declaredDataDependencies']>0,
      'counterHasRealDataflow':counter_graph['counts']['mechanicalDataflows']>0,
      'poisonedLayoutIgnored':stable,
    }
    result['checks']=checks
    result['overall']='PASS_AUDIT_ONLY' if all(checks.values()) else 'FAIL'
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf8')
    print('MCEL_PROMISE_WAVE7B1A',result['overall'])
    for k,v in checks.items():print(f'  {k}: {"PASS" if v else "FAIL"}')
    for k,v in result['cases'].items():
        print(f'  {k}: {v["graph"]["status"]} source={v["sourceTruth"]["status"]}')
    print('report:',output)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    args=parser.parse_args()
    sys.exit(0 if run(args.output)['overall']=='PASS_AUDIT_ONLY' else 1)
