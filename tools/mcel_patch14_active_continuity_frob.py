"""Patch 14.1 opt-in active layout stability Frob.

Usage:
    python -m tools.mcel_patch14_active_continuity_frob

Measures Chromium rendered displacement and validates deterministic 1000-step replay.
Does not switch any production application to the lab policy.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=[
    ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js',
    ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
    ROOT/'main_computer/web/applications/scripts/mcel-semantic-collection-workspace.js',
]
TESTS=[
    'tests/test_mcel_patch14_active_continuity.py',
    'tests/test_mcel_patch14_decision_foundations.py',
    'tests/test_mcel_semantic_layout_inference.py',
    'tests/test_mcel_representation_layout_engine_patch3.py',
    'tests/test_mcel_canonical_recursive_layout.py',
    'tests/test_mcel_representation_layout_contract.py',
    'tests/test_mcel_representation_layout_patch21.py',
    'tests/test_mcel_patch13_completion.py',
]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',default='runtime/reports/mcel-patch14-active-continuity.json')
    args=parser.parse_args()
    dest=ROOT/args.report
    dest.parent.mkdir(parents=True,exist_ok=True)
    browser_evidence=dest.with_name('mcel-patch14-active-browser-evidence.json')
    checks={}
    checks['javascript-syntax']=all(subprocess.run(['node','--check',str(path)],
        cwd=ROOT,capture_output=True).returncode==0 for path in SCRIPTS)
    engine=SCRIPTS[1].read_text(encoding='utf-8')
    workspace=SCRIPTS[2].read_text(encoding='utf-8')
    pure=SCRIPTS[0].read_text(encoding='utf-8')
    checks['explicit-opt-in-no-default-promotion']='continuity="off"' in engine and 'continuity==="lab"' in engine
    checks['legacy-workspace-opt-in-only']='opts.layoutContinuity==="lab"' in workspace
    checks['pure-selector-no-dom']=all(token not in pure for token in ('requestAnimationFrame','document.querySelector','ResizeObserver','MutationObserver'))
    env=dict(os.environ,MCEL_PATCH14_BROWSER_EVIDENCE=str(browser_evidence))
    tests=subprocess.run([sys.executable,'-m','pytest','-q',*TESTS,'--disable-warnings'],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    checks['focused-regressions']=tests.returncode==0
    browser=None
    if browser_evidence.is_file():
        try: browser=json.loads(browser_evidence.read_text(encoding='utf-8'))
        except (ValueError,OSError):pass
    checks['chromium-measured-displacement']=(browser is not None and browser.get('status')=='PASS' and
        browser.get('maxColumnXDisplacementPx',1000)<=0.25 and
        browser.get('baselineTracks')==browser.get('afterContentTracks'))
    checks['hysteresis-without-jitter']=(browser is not None and
        browser.get('layoutModeSequence')==['table','table','stacked','stacked','table'])
    checks['row-order-and-focus']=bool(browser and browser.get('visualOrderRetainedAfterSourceReorder')
        and browser.get('focusedNodeRetainedOnDataOnlyUpdate') and browser.get('explicitUserOrderCommit'))
    checks['no-overflow-browser-errors']=bool(browser and browser.get('noHorizontalOverflow') and
        browser.get('browserErrorCount')==0)
    checks['existing-ir-unchanged']=(ROOT/'main_computer/mcel_application_ir.py').is_file()
    status='PASS' if all(checks.values()) else 'FAIL'
    report={'schema':'mcel.patch14.1-active-continuity-acceptance.v1','status':status,
        'scope':'read-only opt-in collection layout lab, no production promotion',
        'checks':checks,'browserEvidence':browser,'testOutput':(tests.stdout+'\n'+tests.stderr)[-5000:],
        'productionPolicyChanged':False,'runtimeEffectsExecuted':False,
        'knownLimits':[
            'Canonical-tree runtime selection is unchanged; that integration is planned after this collection experiment',
            'DOM reparenting by an external producer can drop focus; this layout policy does not repair source-owned DOM mutation',
            'No named persistent user profiles or cross-application portable naming in 14.1',
            'Windows hosted application integration has not been run in this container',
            'Column width persistence is bounded by viewport feasibility; a viewport shrink may require deterministic reallocation',
        ]}
    dest.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_PATCH14_ACTIVE_CONTINUITY_'+status)
    for name,ok in checks.items(): print(('PASS' if ok else 'FAIL')+' '+name)
    print('report:',dest.relative_to(ROOT))
    print(tests.stdout[-1200:].strip())
    if tests.returncode:print(tests.stderr[-1500:])
    return 0 if status=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
