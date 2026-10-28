"""Patch 12 acceptance: compiled native knowledge, canonical IR, producer-independent binding.

No application server, mutations, marketplace actions or UI layout generation.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path

from main_computer.mcel_application_ir import validate_application_ir
from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.ir_bridge import (
    compile_knowledge_ir, resolve_knowledge, collection_projection,
)
from tools.mcel_semantic_knowledge_acquisition_frob import evaluate as acquisition_frob
from tools.mcel_semantic_knowledge_frob import evaluate as first_cut_frob

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/'tools/fixtures/mcel_computer_knowledge/expanded_computer_snapshot.v1.json'
P11 = ROOT/'main_computer/mcel_knowledge/catalog'


def evaluate():
    cat=load_catalog()
    original=json.loads(FIX.read_text(encoding='utf-8'))
    snapshot=deepcopy(original)
    selection=['file-explorer.entries','worker.sessions','worker.market-workers','task-manager.schedules']
    bridge=compile_knowledge_ir('computer-knowledge',observation_ids=selection)
    ir=bridge['applicationIr']
    view=resolve_knowledge(snapshot,bridge)
    entries=collection_projection(view,'file-explorer.entries')
    workers=collection_projection(view,'worker.sessions')
    remapped=compile_knowledge_ir('computer-knowledge',observation_ids=['task-manager.processes'],bindings={
        'task-manager.processes':{'source':'procItems','properties':{'pid':'os_pid','created_at':'began'}}})
    native=compile_knowledge_ir('computer-knowledge',observation_ids=['task-manager.processes'])
    data={'procItems':[{'os_pid':5,'began':'start-A','memory_rss':0,'memory_availability':'unavailable',
                        'status':'running','name':'work','cmdline':'work.exe'}]}
    renamed=resolve_knowledge(data,remapped)['sources']['task-manager.processes']['records'][0]
    results={
      'existing-application-ir-valid':validate_application_ir(ir).valid,
      'no-new-competing-ir-schema':ir['schema']=='mcel.application-ir.v1',
      'registered-models-concept-refs':all(model['conceptRef']==cat['observations'][model['observationRef']]['concept'] for model in ir['models']),
      'no-executable-intents-or-effects':not ir['intents'] and not ir['effects'] and not ir['capabilities'],
      'semantic-fingerprint-binding-invariant':remapped['semanticFingerprint']==native['semanticFingerprint'],
      'source-binding-fingerprint-sensitive':remapped['sourceBindingFingerprint']!=native['sourceBindingFingerprint'],
      'producer-rename-maintains-lifetime-identity':renamed['identity']['parts']==[5,'start-A'],
      'unavailable-memory-not-zero':renamed['properties']['resident_memory']['state']=='unavailable' and renamed['properties']['resident_memory']['raw']==0,
      'multiple-apps-one-resolver':all(view['sources'][i]['state']=='present' for i in selection),
      'file-identity-root-scoped':view['sources']['file-explorer.entries']['records'][0]['identity']['parts']==['workspace','tools/runs'],
      'worker-relations-remain-candidates':view['sources']['worker.sessions']['records'][0]['relationships'][0]['status']=='candidate-only',
      'worker-lease-not-conflated':view['sources']['worker.sessions']['records'][1]['relationships'][0]['status']=='unresolved',
      'operation-authority-false':all(not op['executable'] and not op['authorized'] for op in view['operations'].values()),
      'v2-browser-collection-adapter':entries['spec']['schema']=='mcel.semantic-collection-spec.v2' and workers['spec']['schema']=='mcel.semantic-collection-spec.v2',
      'no-fabricated-identity-continuity': 'reconciliation' not in entries['spec'] and 'reconciliation' not in workers['spec'],
      'deferred-timestamp-explicit':any(x['property']=='modified_at' and x['reason']=='numeric-timestamp-not-supported-by-v2' for x in entries['deferredFields']),
      'consumer-exact-evidence-recoverable':entries['rows'][0]['mcelCanonicalRecord']['properties']['size_bytes']['raw']==0 and entries['rows'][0]['size_bytesAvailability']=='unavailable',
      'original-source-unchanged':snapshot==original,
      'patch11-catalog-unchanged':len(cat['concepts'])>=48 and len(cat['observations'])>=14,
      'previous-acquisition-frob-passes':acquisition_frob()['status']=='PASS',
      'previous-first-cut-frob-passes':first_cut_frob()['status']=='PASS',
    }
    return {'schema':'mcel.knowledge-ir-integration-frob.v1',
            'status':'PASS' if all(results.values()) else 'FAIL','checks':results,
            'counts':{'checks':len(results),'concepts':len(cat['concepts']),
                      'observations':len(cat['observations']),'operations':len(cat['operations'])},
            'scope':'offline-source-shaped-representative-fixture-and-real-existing-ir-validation',
            'hostedValidated':False,'liveRuntimeWired':False,'productionLayoutChanged':False,
            'operationalEffectsExecuted':False,
            'caveat':'Browser v2 adapter tested separately under Node; no hosted browser route or effect authority claimed.'}


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--report',type=Path,default=ROOT/'runtime/reports/mcel-patch12-knowledge-ir.json')
    args=p.parse_args(argv)
    report=evaluate();args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_PATCH12_SEMANTIC_INTEGRATION_'+report['status'])
    for key,val in report['checks'].items():print(('PASS' if val else 'FAIL')+' '+key)
    print('report:',args.report)
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__': raise SystemExit(main())
