"""P3 evidence gate: real source→IR→generated browser, isolated security tests.

Run: python -m tools.mcel_p3_operational_completion_frob [--report-dir DIR]
Never claims Windows-host, full production parity or human design acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from main_computer.mcel_semantic_generated_host import (compile_generated_candidate,
    compile_p3_generated_candidate, render_p3_generated_page)
from main_computer.mcel_knowledge.semantic_composition import verify_composition

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'runtime/reports/mcel-p3-operational'
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


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report-dir',type=Path,default=DEFAULT)
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
        assert len(p3['intents'])==17
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
                ROOT/'tools/fixtures/mcel_p3_task_manager/application.js',
                ROOT/'main_computer/mcel_knowledge/semantic_composition.py',
                ROOT/'main_computer/task_manager.py',
                ROOT/'main_computer/heartbeat.py')},
        })
        run=subprocess.run([sys.executable,'-m','pytest','--confcutdir=tests','-q',*TESTS],
                           cwd=ROOT,text=True,capture_output=True,timeout=180)
        evidence['pytestReturnCode']=run.returncode
        evidence['pytestTail']=(run.stdout+'\n'+run.stderr)[-8000:]
        if run.returncode==0 and 'skipped' not in run.stdout:
            for k in ('P3_PROCESS_IDENTITY','P3_HTTP_AUTHORITY','P3_HEARTBEAT_AUTHORITY',
                     'P3_READ_EFFECT_PURITY','P3_SCHEDULE','P3_RECEIPTS',
                     'P3_GENERATED_BROWSER','P2_PRESERVATION'):
                statuses[k]='PASS_BOUNDED'
            statuses['P3_BASELINE']='PASS_SNAPSHOT_ONLY'
            ret=0
        else:
            statuses['P3_GENERATED_BROWSER']='FAIL_OR_BLOCKED'
            statuses['P2_PRESERVATION']='FAIL_OR_BLOCKED'
    except Exception as exc:
        evidence['failure']=str(exc)
        statuses['P3_AUTHORING']='FAIL' if statuses['P3_AUTHORING']!='PASS' else 'PASS'
    evidence['knownBlockers']=[
        'No real Windows-host acceptance against the active checkout',
        'No isolated real server lifecycle effect trace or production/P3 operation equivalence',
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
                      'statuses':statuses},indent=2))
    return ret

if __name__=='__main__':raise SystemExit(main())
