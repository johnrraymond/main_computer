#!/usr/bin/env python3
"""07B.1b: bounded semantic-authoring proof; NOT UI synthesis or source validation.

Run: python -m tools.mcel_promise_wave7b1b_frob
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.semantic_graph import audit_application,extract_validated_graph
from tools.mcel_promise_wave7b1a_frob import generated_ir as read_generated_ir, FILE_EXPLORER

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'tests/fixtures/mcel_semantic_synthesis/file_explorer_semantic_only.ir.json'
DEFAULT_OUTPUT=ROOT/'runtime/reports/mcel-promise-wave7b1b/report.json'
PREFIX='file-explorer-semantic-probe'


def triples(graph, kind):
    return {(x['from'],x['to']) for x in graph['edges'] if x['kind']==kind}


def run(output=DEFAULT_OUTPUT):
    raw=json.loads(FIXTURE.read_text(encoding='utf8'))
    valid=validate_application_ir(raw)
    graph=audit_application(raw,catalog=load_catalog())
    production_graph=audit_application(read_generated_ir(FILE_EXPLORER), catalog=load_catalog())
    changed=copy.deepcopy(raw)
    for op in changed['intents']:
        if op['id']==f'intent:{PREFIX}.read-preview':
            op['reads']=[]
    ablated=audit_application(changed)
    poison=copy.deepcopy(valid.normalized) if valid.valid else {}
    poison['surfaces']=[{'semanticSurface':{'applicationShell':{'zone':'fabricated'}}}]
    poison['layouts']=[{'orderedChildren':['fabricated-inspector']}]
    stable=valid.valid and extract_validated_graph(poison)==extract_validated_graph(valid.normalized)
    selects=triples(graph,'bounded-typed-view-selection')
    dataflow=triples(graph,'shared-state-dataflow')
    selected=lambda name:f'intent:{PREFIX}.{name}'
    ablated_dataflow=triples(ablated,'shared-state-dataflow')
    checks={
       'existingIrValid':valid.valid,
       'existingFileExplorerStillInsufficient':production_graph['status']=='SEMANTICS_INSUFFICIENT' and production_graph['catalogJoin']['status']=='BLOCKED_UNJOINED_SOURCES',
       'noAuthoredLayout':not raw.get('surfaces') and not raw.get('layouts') and 'semanticSurface' not in json.dumps(raw),
       'noExecutableBindingOrEffect':not raw.get('effects') and all(not x.get('executionBinding') and not x.get('runtimeMethod') and not x.get('effectRefs') for x in raw['intents']),
       'threeTypedCatalogJoins':graph.get('catalogJoin',{}).get('status')=='JOINED_MODELS_ONLY' and len(graph['catalogJoin']['joins'])==3,
       'twoBoundedTypedSelections':selects=={
          (selected('select-root'),'model:knowledge.file-explorer.roots'),
          (selected('select-entry'),'model:knowledge.file-explorer.entries')},
       'declaredRootToDirectoryRead':(selected('select-root'),selected('list-directory')) in dataflow,
       'declaredEntryToPreviewRead':(selected('select-entry'),selected('read-preview')) in dataflow,
       'readAblationRemovesOnlyDependencies':dataflow-ablated_dataflow=={
          (selected('select-root'),selected('read-preview')),
          (selected('select-entry'),selected('read-preview'))} and ablated_dataflow-dataflow==set(),
       'layoutInvariance':stable,
       'noInventedUiCoordination':graph['counts']['verifiedTaskCoordinations']==0 and not graph['synthesisReady'] and all(not x['impliesUiCoordination'] for x in graph['edges']),
       'noImportedCatalogOperations':graph['catalogJoin']['operationsImported']==0,
    }
    overall='PASS_SEMANTIC_PROBE_ONLY' if all(checks.values()) else 'FAIL'
    result={'schema':'mcel.promise-wave7b1b-authoring-feasibility.v1','generatedUtc':datetime.now(timezone.utc).isoformat(),
            'overall':overall,'claim':'bounded semantic dependency and type crosswalk only; no interface synthesized',
            'sourceTruth':'TEST_FIXTURE_ONLY_NOT_REAL_APPLICATION_SOURCE',
            'fixtureSha256':'sha256:'+hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            'generatedFileExplorerArtifactSha256':'sha256:'+hashlib.sha256(FILE_EXPLORER.read_bytes()).hexdigest(),
            'existingFileExplorerStatus':production_graph['status'],
            'runtimeChanged':False,'synthesizedInterfaces':0,'browserEvidence':'NOT_RUN','hostedEvidence':'NOT_RUN',
            'checks':checks,'graph':graph,'ablation':{'removedDataflow':sorted(list(dataflow-ablated_dataflow))},
            'remainingGaps':[
                'DYNAMIC_RECORD_SELECTION_NOT_SUPPORTED_BY_ENUM_VIEW_TRANSITION',
                'DIRECTORY_READ_RESULT_NOT_DECLARED_AS_DATA_PRODUCER',
                'PREVIEW_READ_RESULT_NOT_DECLARED_AS_DATA_PRODUCER',
                'MODEL_JOIN_DOES_NOT_AUTHORIZE_CATALOG_OPERATIONS',
                'TASK_COORDINATION_AND_INTERFACE_STRUCTURE_NOT_ESTABLISHED',
                'REAL_FILE_EXPLORER_AUTHORING_SOURCE_NOT_VERIFIED',
            ]}
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf8')
    print('MCEL_PROMISE_WAVE7B1B',overall)
    for key,value in checks.items():print(f'  {key}: {"PASS" if value else "FAIL"}')
    print('  outstanding:',', '.join(result['remainingGaps']))
    print('report:',output)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    args=parser.parse_args()
    sys.exit(0 if run(args.output)['overall']=='PASS_SEMANTIC_PROBE_ONLY' else 1)
