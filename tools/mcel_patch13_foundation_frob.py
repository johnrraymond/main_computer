"""Patch 13 stages 1+2 offline acceptance: semantic boundaries and correctness.

No web UI, no scheduler, no Task Manager actions, no hosted assertion.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import unittest

from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir
from tests.test_mcel_patch13_foundations import Stage13FoundationTests
from tools.mcel_knowledge_ir_frob import evaluate as patch12
from tools.mcel_semantic_knowledge_acquisition_frob import evaluate as patch11_acquisition
from tools.mcel_semantic_knowledge_frob import evaluate as patch11_initial

ROOT = Path(__file__).resolve().parents[1]


def evaluate() -> dict:
    cat=load_catalog()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Stage13FoundationTests)
    result=unittest.TestResult(); suite.run(result)
    old12=patch12();old11=patch11_acquisition();old10=patch11_initial()
    derived=cat['observations']['task-manager.processes'].get('derivedProperties',[])
    checks={
        'foundations-test-suite': result.wasSuccessful() and result.testsRun >= 19,
        'single-existing-application-ir':compile_knowledge_ir('patch13-test',observation_ids=['task-manager.processes'])['applicationIr']['schema']=='mcel.application-ir.v1',
        'semantic-boundaries-explicit':bool(derived) and derived[0]['name']=='role',
        'logical-worker-separate-from-lease':'worker.logical-workers' in cat['observations'],
        'patch12-frob-regression':old12['status']=='PASS',
        'patch11-multiapp-frob-regression':old11['status']=='PASS',
        'patch11-initial-frob-regression':old10['status']=='PASS',
    }
    return {
        'schema':'mcel.patch13-foundations-acceptance.v1',
        'status':'PASS' if all(checks.values()) else 'FAIL',
        'stages':['13.0-semantic-boundary','13.1-observation-envelope','13.2-correctness'],
        'checks':checks, 'testCount':result.testsRun,
        'failures':[str(test)+': '+trace for test,trace in result.failures],
        'errors':[str(test)+': '+trace for test,trace in result.errors],
        'counts':{k:len(cat[k]) for k in ('concepts','observations','operations')},
        'applicationIr':'mcel.application-ir.v1',
        'hostedValidated':False, 'liveAdoption':False, 'presentationChanged':False,
        'runtimeEffectsExecuted':False,
        'scope':'offline semantic source formats, source evidence, read-only identity and valid/invalid records; real-host gates pending'
    }


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,
        default=ROOT/'runtime/reports/mcel-patch13-foundations.json')
    args=parser.parse_args(argv)
    data=evaluate();args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print('MCEL_PATCH13_FOUNDATIONS_'+data['status'])
    for key,valid in data['checks'].items(): print(('PASS' if valid else 'FAIL')+' '+key)
    print('focused_tests='+str(data['testCount']), 'concepts='+str(data['counts']['concepts']),
          'observations='+str(data['counts']['observations']))
    print('report:',args.report)
    return 0 if data['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
