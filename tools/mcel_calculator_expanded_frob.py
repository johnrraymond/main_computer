"""Opt-in expanded semantic Calculator acceptance. No automatic promotion.

Without --hosted-url, local Chromium tests do not verify Windows viewport route.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path
from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page, ROUTE
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]

def run(hosted_url: str|None=None) -> int:
    checks={}
    detail=[]
    try:
        ir=compile_expanded()
        checks['pairedCompilation']=len(ir['provenance']['frontend']['sourceFiles'])==2
        checks['semanticHierarchy']=len(ir['surfaces'][0]['interfaceIntent']['regions'])==13
        checks['realCapabilityContracts']=len(ir['intents'])==8 and len(ir['effects'])==7
        checks['noAuthoredLayout']=not ir['layouts']
        checks['genericNumericPlot']=any(n.get('nodeKind')=='plot' for n in ir['surfaces'][0]['nodes'])
        checks['hostAssets']=all(x in render_expanded_page() for x in ['MainComputerCalculatorCore','McelSemanticOperationalRuntime.mount','McelCalculatorExpandedPort.create'])
    except Exception as exc:
        detail.append(str(exc))
        for k in ['pairedCompilation','semanticHierarchy','realCapabilityContracts','noAuthoredLayout','genericNumericPlot','hostAssets']:checks[k]=False
    cmd=[sys.executable,'-m','pytest','-q','-ra', 'tests/test_mcel_calculator_expanded.py']
    try:
        test=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=120,check=False)
        print(test.stdout.strip())
        checks['browserInteractionSuite']=test.returncode==0 and ' passed' in test.stdout and ' skipped' not in test.stdout
        if test.returncode:detail.append((test.stdout+test.stderr)[-1800:])
    except Exception as exc:
        checks['browserInteractionSuite']=False
        detail.append(str(exc))
    if hosted_url is not None:
        checks['realViewportHost']=False
        if hosted_url.rstrip('/').split('?',1)[0].endswith(ROUTE):
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser,backend=launch_semantic_browser(p)
                    try:
                        page=browser.new_page()
                        response=page.goto(hosted_url,timeout=20000)
                        if response and response.status==200:
                            page.locator('form[data-intent="intent:evaluate"] input[name="expression"]').fill('3 m + 40 cm')
                            page.locator('form[data-intent="intent:evaluate"] button[type="submit"]').click()
                            page.locator('[data-state="state:arithmetic-result"]').get_by_text('3.4 m').wait_for(timeout=7000)
                            checks['realViewportHost']=True
                    finally:browser.close()
            except Exception as exc:detail.append('hosted: '+str(exc))
        else:detail.append('Hosted URL must be the exact opt-in Calculator route')
    ok=all(checks.values())
    print('MCEL_CALCULATOR_EXPANDED '+('PASS_EXPANDED_CANDIDATE_ONLY' if ok else 'INCOMPLETE'))
    for k,v in checks.items():print(f'  {k}: {"PASS" if v else "FAIL"}')
    print('  full existing Calculator parity: NOT_VERIFIED')
    print('  optional live Mathics/model endpoints: NOT_VERIFIED')
    print('  expert semantic layout synthesis: NOT_VERIFIED')
    if hosted_url is None:print('  real Windows hosted route: NOT_VERIFIED')
    for line in detail:print('  '+line)
    return 0 if ok else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--hosted-url')
    raise SystemExit(run(parser.parse_args().hosted_url))
