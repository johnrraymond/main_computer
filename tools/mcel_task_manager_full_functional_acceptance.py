"""Task Manager isolated functional acceptance with read-only reactivity shadow.

Example:
  python -m tools.mcel_task_manager_full_functional_acceptance --mode isolated --profile full --timeout 120

The semantic Task Manager UI and backend transport are exercised against
in-memory disposable fixtures. This command never executes host OS mutations.
The shadow observes the incumbent; it neither skips updates nor tests a
candidate renderer. Existing hosted/Windows acceptance is a distinct gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path('runtime/reports/mcel-task-manager-full-functional/report.json')
FULL_TESTS = (
    'tests/test_mcel_task_manager_functional_parity_frob.py',
    'tests/test_mcel_task_manager_navigation_layout.py',
    'tests/test_mcel_task_manager_operational_workspace.py',
    'tests/test_mcel_task_manager_hosted_gate.py',
    'tests/test_mcel_task_manager_functional_shadow.py',
    'tests/test_mcel_reactivity_revision_correlation.py',
    'tests/test_mcel_reactivity_producer_attribution.py',
    'tests/test_mcel_reactivity_semantic_deferred_attribution.py',
)
SMOKE_TESTS = (
    'tests/test_mcel_task_manager_functional_parity_frob.py::test_exact_incumbent_contract_and_paired_intents',
    'tests/test_mcel_task_manager_navigation_layout.py::test_authored_navigation_is_not_an_ordinary_query_control',
    'tests/test_mcel_task_manager_functional_shadow.py::test_oracle_coverage_incomplete_cannot_certify',
    'tests/test_mcel_reactivity_revision_correlation.py::test_revision_correlation_parallel_pending_unknown_async_and_receipts',
    'tests/test_mcel_reactivity_producer_attribution.py::test_direct_scheduled_overlapping_and_unclassified_mutation_partition',
    'tests/test_mcel_reactivity_semantic_deferred_attribution.py::test_exact_deferred_context_and_three_way_attribution',
)
FINGERPRINT_SOURCES = (
    'tools/mcel_task_manager_full_functional_acceptance.py',
    'tools/mcel_task_manager_functional_shadow.py',
    'tests/test_mcel_reactivity_revision_correlation.py',
    'tests/test_mcel_reactivity_producer_attribution.py',
    'tests/test_mcel_reactivity_semantic_deferred_attribution.py',
    'main_computer/web/applications/scripts/mcel-semantic-icon-core.js',
    'main_computer/web/applications/scripts/mcel-semantic-form-presentation-law.js',
    'main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js',
    'tools/mcel_task_manager_functional_parity_frob.py',
    'main_computer/mcel_semantic_other_apps_host.py',
    'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
    'main_computer/web/applications/scripts/mcel-reactivity-shadow-plugin.js',
    'mcel_apps/task-manager/application.js',
    'tools/fixtures/mcel_semantic_other_apps/task-manager/application.js',
)


def subprocess_lane(command: list[str], *, timeout: int, environment=None) -> dict:
    """Bound the child process tree so a stalled browser does not survive a run."""
    started = time.monotonic()
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    if environment is not None:env.update(environment)
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
    with tempfile.TemporaryFile(mode='w+t',encoding='utf-8',errors='replace') as log:
        process = subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            env=env,creationflags=creationflags,start_new_session=(os.name!='nt'))
        expired = False
        try: exit_code=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            expired=True
            if os.name=='nt':
                try: subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=12,check=False)
                except (subprocess.TimeoutExpired,OSError): pass
            else:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait()
            exit_code=None
        log.seek(0)
        output=log.read()
    match=re.search(r'(\d+) passed(?:, (\d+) skipped)?',output)
    return {'status': 'TIMEOUT' if expired else 'PASS' if exit_code==0 else 'FAIL',
            'exitCode':exit_code,'seconds':round(time.monotonic()-started,2),
            'passedTests':int(match.group(1)) if match else 0,
            'outputTail':output[-4000:], 'command':[str(v) for v in command]}


def module_lanes(tests: tuple[str, ...], *, timeout: int, environment: dict) -> dict:
    """Give each independent pytest module its own hard deadline and diagnostic report.

    The former aggregate 120-second deadline could expire after 17 passing
    tests, falsely displaying passed=0 because pytest had not summarized.
    A module hang remains a hard failure; it cannot be hidden by later passes.
    """
    started = time.monotonic()
    lanes = []
    for module in tests:
        command = [sys.executable, '-m', 'pytest', '-q', '--disable-warnings', module]
        lane = subprocess_lane(command, timeout=timeout, environment=environment)
        lane['module'] = module
        # Every selected test module must actually run at least one test.
        if lane['status'] == 'PASS' and lane['passedTests'] == 0:
            lane['status'] = 'FAIL'
            lane['reason'] = 'EMPTY_OR_SKIPPED_TEST_MODULE'
        lanes.append(lane)
        print('TASK_FUNCTIONAL_TEST_MODULE', Path(module.split('::')[0]).name,
              lane['status'], 'passed=' + str(lane['passedTests']),
              'seconds=' + str(lane['seconds']), flush=True)
        if lane['status'] != 'PASS':
            print('TASK_FUNCTIONAL_TEST_MODULE_FAILURE',
                  module, lane.get('reason', ''), lane['outputTail'][-1500:], flush=True)
    return {
        'status': 'PASS' if all(lane['status'] == 'PASS' for lane in lanes) else
                  'TIMEOUT' if any(lane['status'] == 'TIMEOUT' for lane in lanes) else 'FAIL',
        'passedTests': sum(lane['passedTests'] for lane in lanes),
        'seconds': round(time.monotonic() - started, 2),
        'lanes': lanes,
        'outputTail': '\n'.join(lane['module'] + ': ' + lane['status'] + ' ' +
                                lane['outputTail'][-800:] for lane in lanes if lane['status'] != 'PASS')[-4000:],
        'exitCode': 0 if all(lane['status'] == 'PASS' for lane in lanes) else None,
        'command': [sys.executable, '-m', 'pytest', '-q', '--disable-warnings', *tests],
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('isolated',),default='isolated')
    parser.add_argument('--profile',choices=('smoke','full'),default='full')
    parser.add_argument('--timeout',type=int,default=120,help='timeout per test lane, seconds')
    parser.add_argument('--shadow',choices=('on','off'),default='on')
    parser.add_argument('--signals',choices=('off','integrated'),default='off')
    parser.add_argument('--signals-timeout',type=int,default=900)
    parser.add_argument('--report',type=Path,default=DEFAULT)
    args=parser.parse_args(argv)
    if not 10<=args.timeout<=900: parser.error('--timeout must be 10..900')
    if not 15<=args.signals_timeout<=3600: parser.error('--signals-timeout must be 15..3600')
    report_path=args.report if args.report.is_absolute() else ROOT/args.report
    report_path.parent.mkdir(parents=True,exist_ok=True)
    shadow_path=report_path.with_name('report-shadow.json')
    tests=FULL_TESTS if args.profile=='full' else SMOKE_TESTS
    unit=module_lanes(tests,timeout=args.timeout,
        environment={'MCEL_SIGNAL_SCHEDULER_TASK_MANAGER':
                     'certified' if args.signals=='integrated' else 'incumbent'})
    print('TASK_FUNCTIONAL_TESTS',unit['status'],
        'passed='+str(unit['passedTests']),'seconds='+str(unit['seconds']))
    if unit['status']!='PASS': print('TASK_FUNCTIONAL_TEST_TAIL',unit['outputTail'][-1000:])
    shadow={'status':'NOT_RUN','coverageStatus':'NOT_RUN',
            'selectiveSchedulingCertified':False,'renderingEquivalenceTested':False}
    if args.shadow=='on':
        # Never consume a report from a previous checkout or test run.
        shadow_path.unlink(missing_ok=True)
        lane=subprocess_lane([sys.executable,'-m','tools.mcel_task_manager_functional_shadow',
            '--output',str(shadow_path),'--timeout',str(args.timeout)],timeout=args.timeout,
            environment={'MCEL_SIGNAL_SCHEDULER_TASK_MANAGER':'incumbent'})
        print('TASK_FUNCTIONAL_SHADOW_EXECUTION',lane['status'])
        if lane['status']=='PASS' and shadow_path.is_file():
            try:
                evidence=json.loads(shadow_path.read_text(encoding='utf-8'))
                verdict=evidence['evaluation']
                if evidence.get('schema')!='mcel.task-manager.functional-shadow.v1' or (
                    verdict.get('selectiveSchedulingCertified') is not False or
                    verdict.get('renderingEquivalenceTested') is not False):
                    raise ValueError('Invalid shadow schema or unauthorized certification')
                shadow={k:verdict.get(k) for k in (
                    'status','coverageStatus','selectiveSchedulingCertified',
                    'renderingEquivalenceTested','metrics','coverageBlockers','failures')}
                shadow['report']=str(shadow_path)
                shadow['functionalCases']=evidence['functional']['scenarioCount']
                shadow['refreshSteps']=len(evidence['refresh']['steps'])
                shadow['diagnosticPlugin']='mcel-reactivity-shadow'
            except (ValueError,KeyError,TypeError) as exc:
                shadow.update({'status':'FAIL','reason':'INVALID_SHADOW_EVIDENCE:'+str(exc)[:200]})
        else:
            shadow.update({'status':'FAIL','reason':'SHADOW_LANE_'+lane['status'],
                           'outputTail':lane['outputTail'][-1500:]})
        if shadow.get('status')=='PASS_OBSERVATION':
            print('TASK_FUNCTIONAL_SHADOW PASS_OBSERVATION coverage=INCOMPLETE',
                  'notifications='+str(shadow['metrics']['notifications']),
                  'predictedSkips='+str(shadow['metrics']['predictedSkips']),
                  'contradictions='+str(shadow['metrics']['contradictions']),
                  'revisions='+str(shadow['metrics'].get('operationRevisions',0)),
                  'receiptLinks='+str(shadow['metrics'].get('receiptLinks',0)),
                  'asyncTemporal='+str(shadow['metrics'].get('asyncTemporalCandidates',0)),
                  'producerAttributed='+str(shadow['metrics'].get('producerAttributed',0)),
                  'producerUnclassified='+str(shadow['metrics'].get('producerUnclassified',0)),
                  'producerUnclassifiedNoPending='+str(shadow['metrics'].get('producerUnclassifiedNoPending',0)),
                  'operationLinked='+str(shadow['metrics'].get('operationLinkedDom',0)),
                  'independentUi='+str(shadow['metrics'].get('independentUiDom',0)),
                  'unexplained='+str(shadow['metrics'].get('unexplainedDom',0)))
        else: print('TASK_FUNCTIONAL_SHADOW',shadow.get('status'),shadow.get('reason',''))
    state='PASS_ISOLATED_FUNCTIONAL' if unit['status']=='PASS' and (
        args.shadow=='off' or shadow['status']=='PASS_OBSERVATION') else 'FAIL_OR_INCOMPLETE'
    signal={'status':'NOT_REQUESTED','productionDefault':'INCUMBENT'}
    if args.signals=='integrated':
        integrated_path=report_path.with_name('report-signals.json')
        integrated_path.unlink(missing_ok=True)
        lane=subprocess_lane([sys.executable,'-m','tools.mcel_signal_integrated_acceptance',
            '--app','task-manager','--profile',args.profile,'--output',str(integrated_path)],
            timeout=args.signals_timeout,
            environment={'MCEL_SIGNAL_SCHEDULER_TASK_MANAGER':'incumbent'})
        try:
            proof=json.loads(integrated_path.read_text(encoding='utf-8')) if integrated_path.is_file() else {}
            valid=(lane['status']=='PASS' and
                   proof.get('schema')=='mcel.signal-integrated-acceptance.v1' and
                   proof.get('status')=='PASS_MAIN_PATH_INTEGRATION' and
                   len(proof.get('results',[]))==1 and
                   proof['results'][0].get('app')=='task-manager' and
                   proof['results'][0].get('actualSkips',0)>0)
            signal={'status':'PASS_MAIN_PATH_INTEGRATION' if valid else 'BLOCKED',
                'report':str(integrated_path),
                'actualSkips':proof.get('results',[{}])[0].get('actualSkips',0),
                'outputTail':lane['outputTail']}
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            signal={'status':'BLOCKED','reason':type(exc).__name__}
        print('TASK_FUNCTIONAL_SIGNALS',signal['status'],
              'skips='+str(signal.get('actualSkips',0)))
        if signal['status']!='PASS_MAIN_PATH_INTEGRATION':state='FAIL_OR_INCOMPLETE'
    fingerprints={rel:hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
                  for rel in FINGERPRINT_SOURCES if (ROOT/rel).is_file()}
    report={'schema':'mcel.task-manager.full-functional.v1','generatedAt':datetime.now(timezone.utc).isoformat(),
            'mode':args.mode,'profile':args.profile,'status':state,'unit':unit,'shadow':shadow,
            'signalIntegration':signal,
            'fingerprints':fingerprints,
            'boundaries':{'realWindowsHostAcceptance':'NOT_TESTED',
                          'incumbentLegacyBrowserExecution':'NOT_TESTED',
                          'semanticTaskManagerBrowser':'PROMOTED_HOST_IN_MEMORY_TRANSPORT',
                          'hostMutations':'NEVER_DISPATCHED',
                          'renderingEquivalence':('INTEGRATED_DIFFERENTIAL_TESTED'
                            if signal['status']=='PASS_MAIN_PATH_INTEGRATION' else 'NOT_TESTED'),
                          'selectiveScheduling':('EXPLICIT_OPT_IN_TESTED'
                            if signal['status']=='PASS_MAIN_PATH_INTEGRATION' else 'UNCHANGED'),
                          'shadowCoverage':'INCOMPLETE' if args.shadow=='on' else 'NOT_RUN'}}
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('TASK_FUNCTIONAL_OVERALL',state,'report:',report_path)
    return 0 if state=='PASS_ISOLATED_FUNCTIONAL' else 1

if __name__=='__main__':raise SystemExit(main())
