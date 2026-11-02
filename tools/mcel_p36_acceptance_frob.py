"""MCEL P3.6 real-host acceptance with independent fail-closed evidence.

Default is read-only. No production server or schedule mutation is performed.
Examples:
 python -m tools.mcel_p36_acceptance_frob --mode observe --host-url http://127.0.0.1:8765
 python -m tools.mcel_p36_acceptance_frob --mode isolated-effects --host-url http://127.0.0.1:8765 --ack-isolated-effects
 python -m tools.mcel_p36_acceptance_frob --mode certify --review runtime/reports/mcel-p36-acceptance/human-review.json
"""
from __future__ import annotations

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
from datetime import datetime,timezone

from tools.mcel_p36_evidence import (digest,evidence_manifest,gate,init_report,reconcile,
                                    safe_json,set_gate,validate_host_url)
from tools.mcel_p36_parity import source_ledger
from tools.mcel_p36_isolated_effects import isolated_schedule_store,disposable_process_effects

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'runtime/reports/mcel-p36-acceptance'
SOURCES=('tools/fixtures/mcel_p3_task_manager/application.js',
 'main_computer/mcel_semantic_generated_host.py','main_computer/mcel_knowledge/semantic_composition.py',
 'main_computer/task_http_authority.py','main_computer/task_manager.py','main_computer/heartbeat.py',
 'main_computer/viewport_route_dispatch.py','main_computer/web/applications/scripts/mcel-semantic-read-ports.js',
 'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js',
 'tools/mcel_p3_operational_completion_frob.py')


def _bounded(report,out,rerun,bounded_report=None,timeout=180):
    previous=bounded_report or ROOT/'runtime/reports/mcel-p3-operational/report.json'
    if rerun:
        cmd=[sys.executable,'-m','tools.mcel_p3_operational_completion_frob',
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


def run(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('observe','isolated-effects','certify'),default='observe')
    ap.add_argument('--host-url')
    ap.add_argument('--report-dir',type=Path,default=DEFAULT)
    ap.add_argument('--run-bounded',action='store_true',help='Re-run full P3 controlled prerequisite (not live evidence)')
    ap.add_argument('--bounded-report',type=Path,help='Explicit existing PASS_BOUNDARY_ONLY report from this source checkout')
    ap.add_argument('--bounded-timeout',type=int,default=180,help='Maximum seconds for re-running bounded P3 Frob')
    ap.add_argument('--ack-isolated-effects',action='store_true',help='Explicitly permit effects only on harness-owned children')
    ap.add_argument('--review',type=Path)
    ap.add_argument('--carry-report',type=Path,action='append',default=[],
                    help='Previous P3.6 report with same source hashes and verified artifact manifest')
    args=ap.parse_args(argv)
    out=args.report_dir.resolve();out.mkdir(parents=True,exist_ok=True)
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
        safe_json(template,{'schema':'mcel.p36-human-review.v1','approved':False,'reviewerRole':'',
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
            previous=json.loads(supplied.read_text(encoding='utf-8'))
            if previous.get('schema')!='mcel.p36-acceptance.v1' or previous.get('sourceHashes')!=source_hashes:
                raise ValueError('source-or-schema-mismatch')
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
                try:
                    from tools.mcel_p36_hosted_probe import probe
                    hosted=probe(host,out/'screenshots')
                    safe_json(out/'hosted-probe.json',hosted)
                    set_gate(report,'P3_WINDOWS_HOST','PASS' if hosted['passed'] else 'BLOCKED',
                             'Hosted browser passed across five widths' if hosted['passed'] else
                             'Hosted browser incomplete: '+ '; '.join(str(x.get('reason','')) for x in hosted['widths'].values() if not x.get('passed'))[:300],
                             'Check host source parity and browser traces',files=('hosted-probe.json',))
                except Exception as e:
                    set_gate(report,'P3_WINDOWS_HOST','BLOCKED','Hosted probe unavailable: '+str(e)[:300],
                             'Start actual local viewport and rerun')
            if args.mode=='isolated-effects' and args.ack_isolated_effects and report['gates']['P3_WINDOWS_HOST']['status']=='PASS':
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

if __name__=='__main__':raise SystemExit(run())
