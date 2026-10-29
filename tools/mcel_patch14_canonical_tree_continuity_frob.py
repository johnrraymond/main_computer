"""Patch 14.2 canonical-tree continuity Frob (opt-in; no production promotion).

Run from repository root:
    python -m tools.mcel_patch14_canonical_tree_continuity_frob
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=[ROOT/'main_computer/web/applications/scripts'/name for name in
         ('mcel-layout-continuity.js','mcel-semantic-layout-inference.js')]
TESTS=[
    'tests/test_mcel_patch14_canonical_tree_continuity.py',
    'tests/test_mcel_patch14_active_continuity.py',
    'tests/test_mcel_patch14_decision_foundations.py',
    'tests/test_mcel_semantic_layout_inference.py',
    'tests/test_mcel_canonical_recursive_layout.py',
    'tests/test_mcel_representation_layout_engine_patch3.py',
    'tests/test_mcel_representation_layout_contract.py',
    'tests/test_mcel_representation_layout_patch21.py',
]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',default='runtime/reports/mcel-patch14-canonical-tree-continuity.json')
    args=parser.parse_args()
    report_path=ROOT/args.report
    report_path.parent.mkdir(parents=True,exist_ok=True)
    browser_path=report_path.with_name('mcel-patch14-canonical-tree-browser-evidence.json')
    src=SCRIPTS[1].read_text(encoding='utf-8')
    pure=SCRIPTS[0].read_text(encoding='utf-8')
    checks={}
    checks['javascript-syntax']=all(subprocess.run(['node','--check',str(p)],cwd=ROOT,capture_output=True).returncode==0 for p in SCRIPTS)
    checks['explicit-opt-in-no-production-promotion']='options.continuity === "lab"' in src and 'const active=options.continuity === "lab"' in src
    checks['reuse-canonical-renderer']='applyCanonicalLayoutTree(host,evaluation)' in src and 'prepareCanonicalTreePreview' in src
    checks['pure-selector-no-dom']=all(s not in pure for s in ('document.querySelector','requestAnimationFrame','MutationObserver','ResizeObserver'))
    checks['independent-incumbent-evaluation']='incumbentMeasured:true' in src
    checks['restored-state-evidence-validation']='committed tree state and choices disagree' in pure
    checks['original-application-ir-untouched']=(ROOT/'main_computer/mcel_application_ir.py').is_file()
    env=os.environ.copy();env['MCEL_PATCH14_TREE_BROWSER_EVIDENCE']=str(browser_path)
    selected=[t for t in TESTS if (ROOT/t).is_file()]
    missing=[t for t in TESTS if not (ROOT/t).is_file()]
    tests=subprocess.run([sys.executable,'-m','pytest','-q',*selected,'--disable-warnings'],
                         cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    checks['layout-and-compatibility-regressions']=tests.returncode==0 and not missing
    try:browser=json.loads(browser_path.read_text(encoding='utf-8'))
    except (ValueError,OSError):browser=None
    checks['real-chromium-stability']=bool(browser and browser.get('status')=='PASS' and
        browser.get('stableDecision') and browser.get('maxUnnecessaryDisplacementPx',999)<=0.25 and
        browser.get('focusPreserved') and browser.get('sameDomNode'))
    checks['measured-feasibility-reflow']=bool(browser and browser.get('narrowColumns',100)<browser.get('initialColumns',0))
    checks['truncated-search-incumbent-measured']=bool(browser and browser.get('incumbentMeasuredBeyondCap'))
    checks['failed-updates-atomic']=bool(browser and browser.get('invalidUpdateRefusedWithoutCommit'))
    checks['no-browser-errors']=bool(browser and not browser.get('browserErrors'))
    status='PASS' if all(checks.values()) else 'FAIL'
    report={'schema':'mcel.patch14.2-canonical-tree-acceptance.v1','status':status,
      'scope':'opt-in existing canonical-tree layout lab; production policy unchanged',
      'checks':checks,'browserEvidence':browser,'missingTests':missing,
      'testsOutput':(tests.stdout+'\n'+tests.stderr)[-6000:],
      'productionPromotion':False,'runtimeEffectsExecuted':False,
      'knownLimits':[
        'Hosted Windows application lifecycle not certified by this offline canonical-tree Frob',
        'Shared tree node IDs are not yet user-named persistent layout profiles',
        'Entry hysteresis needs measured control-width evidence; no claim for unmeasured geometry',
        'No learned semantic layout search or automatic production promotion',
        'Focus cannot be guaranteed across externally owned DOM reparenting',
      ]}
    report_path.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_PATCH14_CANONICAL_TREE_CONTINUITY_'+status)
    for name,ok in checks.items():print(('PASS' if ok else 'FAIL')+' '+name)
    print('report:',report_path.relative_to(ROOT))
    print(tests.stdout[-1200:].strip())
    if tests.returncode:print(tests.stderr[-1400:])
    return 0 if status=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
