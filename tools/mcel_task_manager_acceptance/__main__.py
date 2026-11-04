"""P3 evidence gate: real source→IR→generated browser, isolated security tests.

Run: python -m tools.mcel_task_manager_acceptance bounded [--report-dir DIR]
Never claims Windows-host, full production parity or human design acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys

from main_computer.mcel_semantic_generated_host import (compile_generated_candidate,
    compile_p3_generated_candidate, render_p3_generated_page)
from main_computer.mcel_knowledge.semantic_composition import verify_composition

ROOT=Path(__file__).resolve().parents[2]
BOUNDED_DEFAULT=ROOT/'runtime/reports/mcel-task-manager-acceptance/bounded'
TESTS=(
    'tests/test_mcel_p3_operational.py',
    'tests/test_mcel_p1_generated_composition.py',
    'tests/test_mcel_p2_monitoring_interactions.py',
    'tests/test_mcel_phase2b_semantic_runtime.py',
    'tests/test_mcel_migration_preservation_patch.py',
    'tests/test_mcel_semantic_other_apps.py',
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report-dir',type=Path,default=BOUNDED_DEFAULT)
    args=ap.parse_args(argv)
    statuses={key:'UNVERIFIED' for key in (
        'P3_BASELINE','P3_PROCESS_IDENTITY','P3_HTTP_AUTHORITY','P3_HEARTBEAT_AUTHORITY',
        'P3_READ_EFFECT_PURITY','P3_SCHEDULE','P3_RECEIPTS','P3_AUTHORING',
        'P3_SYNTHESIS','P3_GENERATED_BROWSER','P2_PRESERVATION',
        'P3_WINDOWS_HOST','P3_REAL_SIDE_EFFECT','P3_PRODUCTION_PARITY','P3_HUMAN_DESIGN')}
    statuses['P3_PROMOTION']='BLOCKED'
    evidence={'scope':'controlled source, service, HTTP heartbeat, and browser tests only',
              'requiresWindowsHost':True,'requiresHumanReview':True,
              'excludedLiveActions':['server_restart','server_shutdown','kill_real_process'],
              'reportType':'bounded evidence, not production certificate'}
    ret=1
    try:
        p3,plan=compile_p3_generated_candidate()
        p2,read_only=compile_generated_candidate()
        verify_composition(p3,plan);verify_composition(p2,read_only)
        assert plan['policy']=='mcel.semantic-structure.p3' and read_only['policy']=='mcel.semantic-structure.p1'
        assert {c['risk'] for c in p2['capabilities']}=={'external-read'}
        assert {c['risk'] for c in p3['capabilities']}=={'external-read','consequential'}
        assert len(p3['intents'])==19
        assert not p3.get('layouts') and not p3['surfaces'][0].get('htmlBindings')
        assert 'window.__mcelP3' in render_p3_generated_page()
        statuses['P3_AUTHORING']='PASS'
        statuses['P3_SYNTHESIS']='PASS'
        evidence.update({'p2SourceIrHash':read_only['sourceIrHash'],
            'p3SourceIrHash':plan['sourceIrHash'],
            'p3CompositionFingerprint':plan['fingerprint'],
            'p3GeneratedRecipes':{r['recipe']:len(r['members']) for r in plan['regions']},
            'intents':sorted(i['id'] for i in p3['intents']),
            'capabilities':{c['id']:c['risk'] for c in p3['capabilities']},
            'sourceHashes':{str(path.relative_to(ROOT)):digest(path) for path in (
                ROOT/'mcel_apps/task-manager/application.js',
                ROOT/'main_computer/mcel_knowledge/semantic_composition.py',
                ROOT/'main_computer/task_manager.py',
                ROOT/'main_computer/heartbeat.py')},
        })
        # Preserve the established, Windows-accepted P3 bounded campaign.
        run=subprocess.run([sys.executable,'-m','pytest','--confcutdir=tests','-q',*TESTS],
                           cwd=ROOT,text=True,capture_output=True,timeout=180)
        evidence['pytestReturnCode']=run.returncode
        pytest_output=run.stdout+'\n'+run.stderr
        evidence['pytestTail']=pytest_output[-8000:]
        evidence['pytestFailures']=re.findall(r'(?m)^(?:FAILED|ERROR)\s+(\S+)',pytest_output)
        run_ok=run.returncode==0 and 'skipped' not in run.stdout
        run_failed=run.returncode!=0
        if run_ok:
            for k in ('P3_PROCESS_IDENTITY','P3_HTTP_AUTHORITY','P3_HEARTBEAT_AUTHORITY',
                     'P3_READ_EFFECT_PURITY','P3_SCHEDULE','P3_RECEIPTS',
                     'P3_GENERATED_BROWSER','P2_PRESERVATION'):
                statuses[k]='PASS_BOUNDED'
            statuses['P3_BASELINE']='PASS_SNAPSHOT_ONLY'
            evidence['boundedGeneratedUiHttpIntegration']={'scenarios':['process-effects','server-lifecycle','scheduling','ai-brief','failure-lifecycle'],
                'execution':'Task Manager route methods + service; controlled OS/Windows/provider boundaries',
                'productionEffectsCertified':False}
            ret=0
        else:
            statuses['P3_BASELINE']='FAIL_BOUNDED' if run_failed else 'INCOMPLETE_BOUNDED'
            statuses['P3_GENERATED_BROWSER']='FAIL_OR_BLOCKED'
            statuses['P2_PRESERVATION']='FAIL_OR_BLOCKED'
    except Exception as exc:
        evidence['failure']=str(exc)
        statuses['P3_AUTHORING']='FAIL' if statuses['P3_AUTHORING']!='PASS' else 'PASS'
    evidence['knownBlockers']=[
        'No real Windows-host acceptance against the active checkout',
        'Bounded generated UI HTTP integration is tested, but production PowerShell controller fidelity and paired consequential OS effects remain unverified',
        'No independently approved human usability/design review',
        'Task Manager legacy hosted suite in export cannot collect without game_projects/webgl-demo/game.json',
        'Multi-process schedule-store locking and OS-user authentication are not certified',
    ]
    outcome='PASS_BOUNDARY_ONLY' if ret==0 else 'FAIL_OR_UNVERIFIED'
    report={'schema':'mcel.p3-operational-frob.v1','outcome':outcome,
            'statuses':statuses,'evidence':evidence}
    args.report_dir.mkdir(parents=True,exist_ok=True)
    (args.report_dir/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    (args.report_dir/'summary.md').write_text('# MCEL P3 evidence\n\n'+
        f'Outcome: {outcome}. This does not authorize production promotion.\n\n'+
        '\n'.join(f'- {k}: {v}' for k,v in statuses.items())+'\n',encoding='utf-8')
    print(json.dumps({'outcome':outcome,'report':str(args.report_dir/'report.json'),
                      'statuses':statuses,
                      **({'failingTests':evidence['pytestFailures']}
                         if evidence.get('pytestFailures') else {})},indent=2))
    return ret



# Consolidated independent live acceptance.
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
import platform
import subprocess
import sys
import uuid
from urllib.request import urlopen
from datetime import datetime,timezone

from .evidence import (digest,evidence_manifest,gate,init_report,reconcile,
                                    safe_json,set_gate,validate_host_url)
from .evidence import source_ledger
from .lab import isolated_schedule_store,disposable_process_effects

ROOT=Path(__file__).resolve().parents[2]
DEFAULT=ROOT/'runtime/reports/mcel-task-manager-acceptance'
SOURCES=('mcel_apps/task-manager/application.js',
 'main_computer/mcel_semantic_generated_host.py','main_computer/mcel_knowledge/semantic_composition.py',
 'main_computer/task_http_authority.py','main_computer/task_manager.py','main_computer/heartbeat.py',
 'main_computer/viewport_route_dispatch.py','main_computer/web/applications/scripts/mcel-semantic-read-ports.js',
 'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
 'tools/mcel_task_manager_acceptance/__main__.py','tools/mcel_task_manager_acceptance/hosted.py',
 'tools/mcel_task_manager_acceptance/lab.py','tools/mcel_task_manager_acceptance/evidence.py',
 'tools/mcel_task_manager_acceptance/evidence.py')


def _bounded(report,out,rerun,bounded_report=None,timeout=180):
    previous=bounded_report or ROOT/'runtime/reports/mcel-task-manager-acceptance/bounded/report.json'
    if rerun:
        cmd=[sys.executable,'-m','tools.mcel_task_manager_acceptance','bounded',
             '--report-dir',str(out/'bounded-prerequisite')]
        try:
            r=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
            (out/'bounded-output.txt').write_text(r.stdout+'\n'+r.stderr,encoding='utf-8')
            previous=out/'bounded-prerequisite/report.json'
        except (subprocess.TimeoutExpired,OSError) as e:
            return {'status':'BLOCKED','reason':f'bounded prerequisite could not execute: {e}'}
    if not previous.exists():return {'status':'BLOCKED','reason':'No recent P3 bounded report; run --run-bounded'}
    try:r=json.loads(previous.read_text(encoding='utf-8'))
    except (ValueError,OSError):return {'status':'BLOCKED','reason':'Bounded report unreadable'}
    if r.get('outcome')!='PASS_BOUNDARY_ONLY':
        return {'status':'FAIL','reason':'P3 bounded prerequisite not passing'}
    for rel,prior_hash in r.get('evidence',{}).get('sourceHashes',{}).items():
        current=ROOT/rel
        if not current.is_file() or digest(current)!=prior_hash:
            return {'status':'BLOCKED','reason':'P3 report refers to earlier source bytes: '+rel+
                    '; rerun --run-bounded'}
    return {'status':'PASS','reason':'P3 bounded result and source hashes verified',
            'sha256':digest(previous),'source':str(previous.relative_to(ROOT)) if previous.is_relative_to(ROOT) else 'run-local'}


def _review(path:Path|None, report, out:Path):
    if not path or not path.exists():
        set_gate(report,'P3_HUMAN_DESIGN','UNVERIFIED','No independently completed review',
                 'Complete human-review-template.json after real UI tasks and screenshots')
        return
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        expected=('find-high-cpu','inspect-identity','safely-refuse-termination','connections',
                  'gpu-unavailable','schedule-in-isolation','deferred-vs-completed','refresh-selection')
        tasks=data.get('tasks',{})
        good=all(isinstance(tasks.get(k),dict) and tasks[k].get('passed') is True and
                 str(tasks[k].get('evidence','')).strip() for k in expected)
        approved=data.get('approved') is True and data.get('reviewerRole') and data.get('reviewedAt')
        if not good or not approved:
            set_gate(report,'P3_HUMAN_DESIGN','BLOCKED','Review is unsigned or tasks lack evidence',
                     'Supply eight task results and explicit human approval')
        else:
            set_gate(report,'P3_HUMAN_DESIGN','PASS','Explicit human rubric sign-off supplied',
                     'Verify review evidence authenticity before separate promotion')
    except Exception as exc:
        set_gate(report,'P3_HUMAN_DESIGN','FAIL','Invalid review: '+str(exc)[:200])


def _expected_host_page_sha256():
    """Expected bytes of this checkout's P3 page; never trust a status alone."""
    from main_computer.mcel_semantic_generated_host import render_p3_generated_page
    return hashlib.sha256(render_p3_generated_page().encode('utf-8')).hexdigest()


def _verify_current_host_page(host_url: str):
    """Read the current loopback host before reusing a prior hosted browser pass."""
    from .hosted import ROUTE
    expected=_expected_host_page_sha256()
    with urlopen(validate_host_url(host_url)+ROUTE, timeout=12) as response:
        if response.status!=200:
            raise ValueError('current-host-page-not-200')
        actual=hashlib.sha256(response.read()).hexdigest()
    if actual!=expected:
        raise ValueError('current-host-source-mismatch')
    return actual


def _verify_carried_hosted_probe(folder: Path, manifest: dict, host_url: str):
    """Require the signed-by-hash artifact and all five real hosted observations."""
    if 'hosted-probe.json' not in manifest:
        raise ValueError('no-hosted-probe-in-evidence-manifest')
    hosted=json.loads((folder/'hosted-probe.json').read_text(encoding='utf-8'))
    from .hosted import WIDTHS,ROUTE
    expected=_expected_host_page_sha256()
    if (hosted.get('host')!=validate_host_url(host_url) or hosted.get('route')!=ROUTE
            or hosted.get('passed') is not True):
        raise ValueError('hosted-evidence-host-route-or-verdict-mismatch')
    widths=hosted.get('widths',{})
    if set(widths)!={str(width) for width in WIDTHS}:
        raise ValueError('hosted-evidence-viewport-set-mismatch')
    for width in WIDTHS:
        item=widths[str(width)]
        if (item.get('passed') is not True or item.get('hostPageSha256')!=expected
                or item.get('expectedPageSha256')!=expected):
            raise ValueError('hosted-evidence-viewport-or-source-mismatch:'+str(width))
    if widths['1100'].get('osPidConfirmed') is not True:
        raise ValueError('hosted-evidence-missing-independent-process-identity')
    return expected


def run(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('observe','isolated-effects','certify'),default='observe')
    ap.add_argument('--host-url')
    ap.add_argument('--report-dir',type=Path,default=None)
    ap.add_argument('--run-bounded',action='store_true',help='Re-run full P3 controlled prerequisite (not live evidence)')
    ap.add_argument('--bounded-report',type=Path,help='Explicit existing PASS_BOUNDARY_ONLY report from this source checkout')
    ap.add_argument('--bounded-timeout',type=int,default=180,help='Maximum seconds for re-running bounded P3 Frob')
    ap.add_argument('--ack-isolated-effects',action='store_true',help='Explicitly permit effects only on harness-owned children')
    ap.add_argument('--review',type=Path)
    ap.add_argument('--carry-report',type=Path,action='append',default=[],
                    help='Previous P3.6 report with same source hashes and verified artifact manifest')
    args=ap.parse_args(argv)
    out=(args.report_dir or (DEFAULT/('run-'+uuid.uuid4().hex))).resolve()
    if (out/'report.json').exists():
        raise ValueError('Existing acceptance report is immutable; use a fresh --report-dir')
    out.mkdir(parents=True,exist_ok=True)
    source_hashes={rel:digest(ROOT/rel) for rel in SOURCES}
    report=init_report(uuid.uuid4().hex,source_hashes)
    report['mode']=args.mode;report['runner']={'platform':platform.platform(),'python':sys.version.split()[0]}
    report['observedAt']=datetime.now(timezone.utc).isoformat()
    bounded=_bounded(report,out,args.run_bounded,args.bounded_report,args.bounded_timeout)
    report['boundedPrerequisite']=bounded
    if bounded['status']=='PASS':
        set_gate(report,'P36_BASELINE','PASS','Bounded prerequisite and declared source inventory measured',
                 'Validate active running host page hash and checkout before promotion')
    else:
        set_gate(report,'P36_BASELINE',bounded['status'],bounded['reason'],'Run P3 prerequisite on this checkout')

    ledger=source_ledger(ROOT);safe_json(out/'parity-ledger.json',ledger)
    if ledger['gaps']:
        set_gate(report,'P3_PRODUCTION_PARITY','BLOCKED','Source parity gaps: '+', '.join(ledger['gaps']),
                 'Repair missing capabilities then compare actual operator workflows',files=('parity-ledger.json',))
    else:
        set_gate(report,'P3_PRODUCTION_PARITY','UNVERIFIED','Source inventory complete; live incumbent comparison absent',
                 'Capture real production and P3 operation traces',files=('parity-ledger.json',))

    # Inspecting real UI screenshots is a human act, never automatically certified.
    template=out/'human-review-template.json'
    if not template.exists():
        safe_json(template,{'schema':'mcel.task-manager-human-review.v1','approved':False,'reviewerRole':'',
            'reviewedAt':'','tasks':{k:{'passed':False,'evidence':'','notes':''} for k in (
                'find-high-cpu','inspect-identity','safely-refuse-termination','connections',
                'gpu-unavailable','schedule-in-isolation','deferred-vs-completed','refresh-selection')},
            'note':'Only human operator should complete after real hosted screenshots/tasks.'})
    _review(args.review,report,out)

    # Combine independent prior runs only with verified file hashes, identical
    # source fingerprints, and a bounded freshness window. Neither a plain
    # status copied from a prior report nor a replaced evidence file can pass.
    carry=[]
    for supplied in args.carry_report:
        try:
            if supplied.resolve()==(out/'report.json').resolve():
                raise ValueError('carry-report-is-current-output; use distinct --report-dir per mode')
            previous=json.loads(supplied.read_text(encoding='utf-8'))
            if previous.get('schema')!='mcel.task-manager-acceptance.v1' or previous.get('sourceHashes')!=source_hashes:
                raise ValueError('source-or-schema-mismatch')
            if args.host_url and previous.get('host')!=validate_host_url(args.host_url):
                raise ValueError('carried-evidence-host-mismatch')
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(previous['createdAt'])).total_seconds()
            if not 0<=age<=86400:raise ValueError('stale-evidence-older-than-one-day')
            manifest=previous.get('artifactManifest',{})
            if not manifest:raise ValueError('missing-artifact-manifest')
            for relative,expected_hash in manifest.items():
                source=(supplied.parent/relative).resolve()
                if not source.is_relative_to(supplied.parent.resolve()) or not source.is_file() or digest(source)!=expected_hash:
                    raise ValueError('evidence-hash-mismatch')
            for name in ('P3_WINDOWS_HOST','P36_PROCESS_EFFECT','P36_SCHEDULE_EFFECT','P36_SERVER_LIFECYCLE_EFFECT',
                         'P3_PRODUCTION_PARITY'):
                old=previous.get('gates',{}).get(name,{})
                if old.get('status')=='PASS':
                    if name=='P3_WINDOWS_HOST':
                        if not args.host_url:
                            raise ValueError('host-url-required-for-hosted-evidence-carry')
                        _verify_carried_hosted_probe(supplied.parent,manifest,args.host_url)
                    set_gate(report,name,'PASS','Verified prior evidence from '+previous.get('runId','unknown'),
                             'Confirm source/host equivalence and human acceptance before promotion')
            carry.append({'runId':previous.get('runId'),'status':'VERIFIED','sha256':digest(supplied)})
        except Exception as exc:
            carry.append({'source':str(supplied.name),'status':'REFUSED','reason':str(exc)[:200]})
    report['carriedEvidence']=carry

    # Sandboxed schedule storage is safe, but not a production HTTP or scheduler execution proof.
    try:
        schedule=isolated_schedule_store()
        safe_json(out/'isolated-schedule.json',schedule)
        set_gate(report,'P36_SCHEDULE_EFFECT',schedule['status'],schedule['reason'],
                 'Verify isolated HTTP route and scheduler lifecycle separately',files=('isolated-schedule.json',))
    except Exception as e:
        set_gate(report,'P36_SCHEDULE_EFFECT','FAIL','Schedule lab error: '+str(e)[:200])

    if report['gates']['P36_SERVER_LIFECYCLE_EFFECT']['status']!='PASS':
        set_gate(report,'P36_SERVER_LIFECYCLE_EFFECT','BLOCKED','No demonstrably isolated server controller exercised',
                 'Launch dedicated disposable controller and prove start/restart/shutdown by PID and health traces')
    if report['gates']['P36_PROCESS_EFFECT']['status']!='PASS':
        set_gate(report,'P36_PROCESS_EFFECT','BLOCKED','Real effects require explicit opt-in and a verified live host',
                 'Run --mode isolated-effects with --ack-isolated-effects on the local host')

    if args.host_url:
        try:host=validate_host_url(args.host_url)
        except ValueError as e:
            set_gate(report,'P3_WINDOWS_HOST','FAIL',str(e));host=None
        if host:
            report['host']=host
            if args.mode in ('observe','isolated-effects'):
                already_verified=(args.mode=='isolated-effects' and
                    report['gates']['P3_WINDOWS_HOST']['status']=='PASS')
                if already_verified:
                    # The prior five-width browser evidence was checked against its
                    # immutable artifact hashes above. Recheck the *currently running*
                    # page, but do not force a second full UI campaign merely to
                    # verify disposable process effects.
                    try:
                        page_hash=_verify_current_host_page(host)
                        report['currentHostPageSha256']=page_hash
                    except Exception as e:
                        set_gate(report,'P3_WINDOWS_HOST','BLOCKED','Carried host was not reverified: '+str(e)[:300],
                                 'Check active viewport source and rerun observe')
                else:
                    try:
                        from .hosted import probe
                        hosted=probe(host,out/'screenshots')
                        safe_json(out/'hosted-probe.json',hosted)
                        set_gate(report,'P3_WINDOWS_HOST','PASS' if hosted['passed'] else 'BLOCKED',
                                 'Hosted browser passed across five widths' if hosted['passed'] else
                                 'Hosted browser incomplete: '+ '; '.join(str(x.get('reason','')) for x in hosted['widths'].values() if not x.get('passed'))[:300],
                                 'Check host source parity and browser traces',files=('hosted-probe.json',))
                    except Exception as e:
                        set_gate(report,'P3_WINDOWS_HOST','BLOCKED','Hosted probe unavailable: '+str(e)[:300],
                                 'Start actual local viewport and rerun')
            # Independent live authority and OS identity checks belong to
            # disposable_process_effects. A transient viewport failure must not
            # suppress a separately acknowledged, run-owned child-process test.
            if args.mode=='isolated-effects' and args.ack_isolated_effects:
                try:
                    effect=disposable_process_effects(host,ack=True)
                    safe_json(out/'disposable-effects.json',effect)
                    set_gate(report,'P36_PROCESS_EFFECT',effect['status'],effect['reason'],
                             'Confirm generated browser click path independently',files=('disposable-effects.json',))
                except Exception as e:
                    set_gate(report,'P36_PROCESS_EFFECT','FAIL','Disposable test error: '+str(e)[:300])
    elif args.mode in ('observe','isolated-effects'):
        set_gate(report,'P3_WINDOWS_HOST','BLOCKED','Explicit --host-url required',
                 'Provide real local viewport URL and port')
    elif report['gates']['P3_WINDOWS_HOST']['status']!='PASS':
        set_gate(report,'P3_WINDOWS_HOST','UNVERIFIED','Certify requires verified carried hosted evidence',
                 'Run live observe and supply --carry-report with verified hashes')
    if args.mode=='isolated-effects' and not args.ack_isolated_effects:
        set_gate(report,'P36_PROCESS_EFFECT','BLOCKED','Missing explicit --ack-isolated-effects')

    reconcile(report)
    (out/'summary.md').write_text('# MCEL P3.6 acceptance\n\n'+
        'Outcome: '+report['outcome']+' (never an automatic promotion).\n\n'+
        '\n'.join(f'- {k}: {v["status"]} — {v["reason"]}' for k,v in report['gates'].items())+'\n',encoding='utf-8')
    report['artifactManifest']=evidence_manifest(out,exclude=('report.json','evidence-manifest.json'))
    safe_json(out/'evidence-manifest.json',report['artifactManifest'])
    safe_json(out/'report.json',report)
    print(json.dumps({'outcome':report['outcome'],'report':str(out/'report.json'),
          'statuses':{k:v['status'] for k,v in report['gates'].items()}},indent=2))
    return 1 if report['outcome']=='FAIL_ACCEPTANCE' else 0




def _parity_console_summary(result, report_path):
    """Keep BLOCKED separate from UNVERIFIED, with actionable refusal reasons.

    A workflow that the runner deliberately refuses is not unverified, but it
    must not disappear from the console just because it has no failing test.
    """
    rows=result.get('rows',{})
    names=('passed','failed','blocked','unverified')
    groups={name:list(result.get(name,[])) for name in names}
    if len({workflow for group in groups.values() for workflow in group}) != sum(
            len(group) for group in groups.values()):
        raise ValueError('duplicate-workflow-accounting')
    if rows and set(rows) != {workflow for group in groups.values() for workflow in group}:
        raise ValueError('missing-workflow-accounting')
    return {'report':str(report_path),'status':result['status'],
            'passed':groups['passed'],'failed':groups['failed'],
            'blocked':groups['blocked'],'unverified':groups['unverified'],
            'blockedReasons':{name:rows.get(name,{}).get('reason','Evidence unavailable')
                              for name in groups['blocked']},
            'reason':result.get('reason','')}

def integration_main(argv=None):
    """Run the source-based, bounded generated-UI operational HTTP contracts.

    This is interface integration evidence only. It must not open a production
    viewport, dispatch a Windows PowerShell action or promote an app.
    """
    ap=argparse.ArgumentParser(description='Bounded generated Task Manager operational integration')
    ap.add_argument('--report-dir',type=Path)
    ap.add_argument('--timeout',type=int,default=180)
    args=ap.parse_args(argv)
    if not 20 <= args.timeout <= 900:
        ap.error('timeout must be 20–900 seconds')
    output=(args.report_dir or DEFAULT/('integration-'+uuid.uuid4().hex)).resolve()
    if (output/'report.json').exists():
        raise ValueError('Existing integration report is immutable')
    output.mkdir(parents=True, exist_ok=True)
    selected=('tests/test_mcel_p3_operational.py',)
    command=[sys.executable,'-m','pytest','--confcutdir=tests','-q',
             *selected,'-k','test_p3_real_http_']
    reason=''
    try:
        proc=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=args.timeout)
        logs=proc.stdout+'\n'+proc.stderr
        # Zero return code is insufficient if tests were deselected or skipped.
        verified=(proc.returncode==0 and '5 passed' in proc.stdout and
                  'skipped' not in proc.stdout and 'xfailed' not in proc.stdout)
        status='PASS_BOUNDED_INTEGRATION' if verified else 'FAIL_OR_UNVERIFIED'
        reason='Five browser/HTTP/service contract tests passed' if verified else 'Integration tests failed or were not all executed'
        returncode=proc.returncode
    except (subprocess.TimeoutExpired,OSError) as exc:
        status='BLOCKED'
        reason='Integration test execution unavailable: '+str(exc)[:300]
        logs=str(exc)
        returncode=None
    (output/'pytest-output.txt').write_text(logs,encoding='utf-8')
    report={'schema':'mcel.task-manager-bounded-integration.v1',
            'status':status,'reason':reason,'pytestReturnCode':returncode,
            'tests':'tests/test_mcel_p3_operational.py -k test_p3_real_http_',
            'scenarios':['process-effects','server-lifecycle','scheduling','ai-brief','failure-lifecycle'],
            'executionBoundary':'real generated JS + isolated HTTP Task Manager route methods + TaskManagerService; test-owned OS/controller/provider adapters',
            'browserTransport':'Python HTTP forwarding for CI browser loopback restrictions; no simulated backend response',
            'notCertified':['real Windows PowerShell controller fidelity','live AI provider',
                            'unrestricted OS processes','human design approval','production replacement'],
            'promotionAuthorized':False,
            'sourceHashes':{rel:digest(ROOT/rel) for rel in (
                'tests/test_mcel_p3_operational.py',
                'main_computer/task_manager.py','main_computer/viewport_routes_task.py',
                'main_computer/task_http_authority.py',
                'mcel_apps/task-manager/application.js',
                'main_computer/web/applications/scripts/mcel-semantic-read-ports.js',
                'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js')},
            'pytestOutputSha256':digest(output/'pytest-output.txt')}
    safe_json(output/'report.json',report)
    print(json.dumps({'status':status,'report':str(output/'report.json'),
                      'reason':reason,'scenarios':report['scenarios'],
                      'notCertified':report['notCertified'],
                      'promotionAuthorized':False},indent=2))
    return 0 if status=='PASS_BOUNDED_INTEGRATION' else 1


def main(argv=None):
    """Single stable CLI. Each mode has independent evidence, never auto-promotes."""
    arguments=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(description='Canonical Task Manager acceptance')
    parser.add_argument('command',choices=('bounded','integration','observe','effects','lifecycle','parity','certify','diagnose'))
    known,_=parser.parse_known_args(arguments[:1])
    rest=arguments[1:]
    if known.command=='bounded':return bounded_main(rest)
    if known.command=='integration':return integration_main(rest)
    if known.command=='diagnose':
        if rest:raise ValueError('diagnose takes no arguments')
        from .lab import diagnose
        return diagnose()
    if known.command=='lifecycle':
        p=argparse.ArgumentParser(description='Disposable task-only service lifecycle')
        p.add_argument('--ack-isolated-effects',action='store_true')
        p.add_argument('--report-dir',type=Path)
        a=p.parse_args(rest)
        from .lab import isolated_server_lifecycle
        result=isolated_server_lifecycle(ack=a.ack_isolated_effects)
        out=(a.report_dir or DEFAULT/('lifecycle-'+uuid.uuid4().hex)).resolve()
        if (out/'report.json').exists():raise ValueError('Evidence report exists')
        safe_json(out/'report.json',result)
        print(json.dumps({'report':str(out/'report.json'),**result},indent=2))
        return 1 if result['status']=='FAIL' else 0
    if known.command=='parity':
        p=argparse.ArgumentParser(description='Dual UI workflow parity, no production mutations')
        p.add_argument('--host-url',required=True)
        p.add_argument('--ack-isolated-effects',action='store_true')
        p.add_argument('--report-dir',type=Path)
        a=p.parse_args(rest)
        from .hosted import live_readonly_parity
        out=(a.report_dir or DEFAULT/('parity-'+uuid.uuid4().hex)).resolve()
        if (out/'report.json').exists():raise ValueError('Evidence report exists')
        # --ack-isolated-effects is never authority to mutate the production
        # host. Consequential UI parity requires a separate attested lab, which
        # this runner deliberately cannot silently construct from a port.
        if a.ack_isolated_effects:
            result={'schema':'mcel.task-manager-live-parity.v2','status':'BLOCKED',
                    'reason':'Effects acknowledgment refused: parity on --host-url is read-only; no verified isolated lab host',
                    'passed':[],'failed':[],'blocked':[],
                    'unverified':['overview-parameters','four-views','refresh','inspection',
                                  'responsive','scheduling','ai-brief','process-effects',
                                  'failure-lifecycle','server-lifecycle'],
                    'productionMutations':False}
            safe_json(out/'report.json',result)
            print(json.dumps({'report':str(out/'report.json'),**result},indent=2))
            return 0
        result=live_readonly_parity(a.host_url,out)
        safe_json(out/'report.json',result)
        print(json.dumps(_parity_console_summary(result, out/'report.json'),indent=2))
        return 1 if result['status']=='FAIL' else 0
    selected={'observe':'observe','effects':'isolated-effects','certify':'certify'}[known.command]
    return run(['--mode',selected,*rest])

if __name__=='__main__':raise SystemExit(main())
