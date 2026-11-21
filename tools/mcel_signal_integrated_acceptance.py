"""Patch 4: explicitly opted-in signal scheduling on normal MCEL application mounts.

Runs the actual hosted SLH and Task Manager mounting code in independent Chromium
roots. No real host actions are dispatched. For --profile full each supported
consumer class is independently retested with failure/recovery/remount stress.
A PASS here is *main-path integration acceptance*, not production cutover.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from tools.mcel_signal_consumer_certification import certify
from tools.mcel_signal_differential_acceptance import ROOT, run_differential

DEFAULT = Path('runtime/reports/mcel-signal-integration/report.json')


def accept(*,apps=('slh','task-manager'),profile='full',repo=ROOT):
    if profile not in ('smoke','full') or not apps or any(
        a not in ('slh','task-manager') for a in apps):
        raise ValueError('INVALID_INTEGRATED_ACCEPTANCE_PARAMETERS')
    results=[]
    for app in apps:
        try:
            paired=run_differential(app,repo,candidate_mode='integrated',stress=True)
            problems=list(paired.get('failures',[]))
            candidate=paired.get('candidate',{})
            if paired.get('status')!='PASS_DIFFERENTIAL':problems.append('MAIN_PATH_DIFFERENTIAL_FAILED')
            if candidate.get('mode')!='integrated-opt-in':problems.append('MAIN_PATH_NOT_ENABLED')
            if candidate.get('actualSkips',0)<=0:problems.append('NO_ACTUAL_SKIPS')
            if not paired.get('boundaries',{}).get('mainPathOptInExercised'):
                problems.append('INTEGRATION_BOUNDARY_NOT_EXERCISED')
            cert_report=None
            if profile=='full':
                cert_report=certify(repo=repo,apps=(app,),candidate_mode='integrated')
                if cert_report.get('status')!='PASS_INTEGRATED_CERTIFICATION':
                    problems.append('CLASS_CERTIFICATION_FAILED')
                if not any(x.get('status')=='PASS_INTEGRATED_CLASS'
                           for x in cert_report.get('classes',[])):
                    problems.append('NO_APPLICABLE_CERTIFIED_CLASS')
            results.append({'app':app,'status':'PASS_MAIN_PATH_INTEGRATED' if not problems else 'BLOCKED',
                'failures':problems,'actualSkips':candidate.get('actualSkips',0),
                'differences':sum(s.get('differenceCount',0) for s in paired.get('stages',[])),
                'differential':paired,'certification':cert_report})
        except Exception as exc:
            results.append({'app':app,'status':'BLOCKED',
                'failures':[type(exc).__name__+': '+str(exc)[:1000]],
                'actualSkips':0,'differences':None})
    passed=len(results)==len(apps) and all(x['status']=='PASS_MAIN_PATH_INTEGRATED' for x in results)
    return {'schema':'mcel.signal-integrated-acceptance.v1',
        'generatedAt':datetime.now(timezone.utc).isoformat(),'profile':profile,
        'status':'PASS_MAIN_PATH_INTEGRATION' if passed else 'BLOCKED',
        'results':results,
        'boundaries':{'productionDefault':'INCUMBENT','productionCutoverAuthorized':False,
            'realHostAcceptance':'NOT_TESTED','hostMutations':'NEVER_DISPATCHED',
            'backendTransport':'TWO_IN_MEMORY_FIXTURES',
            'normalApplicationMount':'OPTED_IN_CANDIDATE_EXECUTION',
            'automaticFallback':'INCUMBENT_ON_UNKNOWN_INPUTS',
            'strictPixelParityCertified':False}}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',choices=('slh','task-manager','both'),default='both')
    parser.add_argument('--profile',choices=('smoke','full'),default='full')
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args(argv)
    apps=('slh','task-manager') if args.app=='both' else (args.app,)
    report=accept(apps=apps,profile=args.profile)
    output=args.output if args.output.is_absolute() else ROOT/args.output
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    for result in report['results']:
        print('MCEL_SIGNAL_MAIN_PATH',result['app'],result['status'],
            'skips='+str(result['actualSkips']),
            'differences='+str(result['differences']),flush=True)
    print('MCEL_SIGNAL_MAIN_PATH_OVERALL',report['status'],'report:',output,flush=True)
    return 0 if report['status']=='PASS_MAIN_PATH_INTEGRATION' else 1

if __name__=='__main__':
    raise SystemExit(main())
