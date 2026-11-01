"""Opt-in semantic expert presentation proof (not full Calculator parity)."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path
from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page, ROUTE

ROOT=Path(__file__).resolve().parents[1]

def run(hosted_url: str|None=None) -> int:
    checks={}
    issues=[]
    try:
        ir=compile_expanded()
        intent=ir['surfaces'][0]['interfaceIntent']
        checks['pairedSemanticCompilation']=len(ir['provenance']['frontend']['sourceFiles'])==2
        checks['layoutFreeSource']=not ir['layouts']
        checks['computeTaskAvailable']=any('compute' in r.get('tasks',[]) and r.get('priority')=='primary' for r in intent['regions'])
        checks['authorityUnchanged']={e['risk'] for e in ir['effects']}=={'external-read'}
        html=render_expanded_page()
        checks['genericExpertPresentation']='mcel-semantic-expert.css' not in html and 'data-mcel-expression-pad' in html and "presentation:'expert'" in html
        checks['noAuthoredFixedLayout']='grid-template-areas' not in html and 'semantic-calculator-expanded' not in (ROOT/'main_computer/web/applications/styles/mcel-semantic-expert.css').read_text()
    except Exception as exc:
        issues.append(str(exc))
        for k in ['pairedSemanticCompilation','layoutFreeSource','computeTaskAvailable','authorityUnchanged','genericExpertPresentation','noAuthoredFixedLayout']:checks.setdefault(k,False)
    tests=['tests/test_mcel_calculator_expert_presentation.py','tests/test_mcel_calculator_expanded.py','tests/test_mcel_phase2c_hosted_calculator.py']
    try:
        result=subprocess.run([sys.executable,'-m','pytest','-q',*tests],cwd=ROOT,capture_output=True,text=True,timeout=120,check=False)
        print(result.stdout.strip())
        checks['realBrowserSuite']=result.returncode==0 and ' passed' in result.stdout
        if result.returncode:issues.append((result.stdout+result.stderr)[-2500:])
    except Exception as exc:
        checks['realBrowserSuite']=False
        issues.append(str(exc))
    if hosted_url is not None:
        checks['hostedRoute']=False
        if hosted_url.rstrip('/').split('?',1)[0].endswith(ROUTE):
            try:
                from playwright.sync_api import sync_playwright
                from tools.mcel_phase2b_browser import launch_semantic_browser
                with sync_playwright() as p:
                    b,_=launch_semantic_browser(p)
                    try:
                        page=b.new_page(viewport={'width':1100,'height':850})
                        res=page.goto(hosted_url,timeout=20000)
                        if res and res.status==200:
                            pad=page.locator('[data-mcel-expression-pad]')
                            for action in ('7','+','8','submit'):
                                pad.locator(f'button[data-pad-action="{action}"]').click()
                            page.locator('[data-state="state:arithmetic-result"]').get_by_text('15').wait_for(timeout=7000)
                            checks['hostedRoute']=True
                    finally:b.close()
            except Exception as exc:issues.append('hosted: '+str(exc))
        else:issues.append('Unexpected hosted URL path')
    success=all(checks.values())
    print('MCEL_CALCULATOR_EXPERT '+('PASS_SEMANTIC_PRESENTATION_CANDIDATE_ONLY' if success else 'INCOMPLETE'))
    for k,v in checks.items():print(f'  {k}: {"PASS" if v else "FAIL"}')
    if hosted_url is None:print('  live Windows hosted route: NOT_VERIFIED')
    print('  full legacy Calculator parity: NOT_VERIFIED')
    print('  four-app expert layout: NOT_VERIFIED')
    print('  human usability acceptance: PENDING')
    for issue in issues:print('  '+issue)
    return 0 if success else 1

if __name__=='__main__':
    arg=argparse.ArgumentParser()
    arg.add_argument('--hosted-url')
    raise SystemExit(run(arg.parse_args().hosted_url))
