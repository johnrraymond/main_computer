"""Phase 2C opt-in hosted candidate, including real-core browser proof.

Without --hosted-url, passing local tests is NOT proof of real viewport route.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_phase2c_candidate_host import compile_candidate, render_candidate_page
from tools.mcel_phase2b_browser import BrowserUnavailable, launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]


def run(hosted_url: str | None = None) -> int:
    checks: dict[str, bool] = {}
    diagnostic = ""
    try:
        ir = compile_candidate()
        checks['pairedSource'] = bool(ir['surfaces'][0]['interfaceIntent']['hierarchy'])
        checks['boundedAuthority'] = len(ir['effects']) == 1 and ir['effects'][0]['risk'] == 'external-read'
        checks['selfContainedHostPage'] = 'McelSemanticOperationalRuntime.mount' in render_candidate_page()
    except Exception as exc:
        diagnostic = str(exc)
        checks['pairedSource'] = False
        checks['boundedAuthority'] = False
        checks['selfContainedHostPage'] = False
    cmd = [sys.executable, '-m', 'pytest', '-q', '-ra', 'tests/test_mcel_phase2c_hosted_calculator.py']
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
    checks['browserCandidateTests'] = result.returncode == 0 and ' passed' in result.stdout and ' skipped' not in result.stdout
    print(result.stdout.strip())
    if result.returncode:
        diagnostic += '\n' + (result.stderr or '')[-1200:]

    if hosted_url is not None:
        checks['realViewportHost'] = False
        if hosted_url.rstrip('/').split('?', 1)[0].endswith('/applications/mcel-semantic-calculator-candidate'):
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser, backend = launch_semantic_browser(p)
                    try:
                        page = browser.new_page()
                        response = page.goto(hosted_url, timeout=20000)
                        if response and response.status == 200:
                            page.locator('input[name="expression"]').fill('6 * 7')
                            page.locator('form[data-intent="intent:compute"] button').click()
                            page.locator('[data-state="state:result"]').get_by_text('42').wait_for(timeout=10000)
                            checks['realViewportHost'] = page.locator('[data-disposition="completed"]').count() > 0
                        page.close()
                    finally:
                        browser.close()
            except Exception as exc:
                diagnostic += '\nHosted route: ' + str(exc)[:600]
        else:
            diagnostic += '\nHosted URL must point to the exact semantic Calculator candidate route'

    base = all(v for k, v in checks.items() if k != 'realViewportHost')
    hosted_pass = checks.get('realViewportHost') is True
    status = ('PASS_HOSTED_CANDIDATE_ONLY' if base and hosted_pass else
              'PASS_LOCAL_CANDIDATE_ONLY' if base and hosted_url is None else 'INCOMPLETE')
    out = ROOT / 'runtime/reports/mcel-phase2c'
    out.mkdir(parents=True, exist_ok=True)
    (out/'report.json').write_text(json.dumps({
        'schema':'mcel.phase2c-hosted-candidate-report.v1', 'status':status,
        'checks':checks, 'diagnostic': diagnostic,
        'productionCalculatorMigrated':False,'fourAppParity':'NOT_VERIFIED',
        'expertLayout':'NOT_VERIFIED',
        'candidateSource':'tools/fixtures/mcel_phase2_reference_apps/calculator/application.js',
        'productionCore':'main_computer/web/applications/scripts/calculator-core.js',
    },indent=2,sort_keys=True)+'\n',encoding='utf8')
    print('MCEL_PHASE2C_HOSTED_CANDIDATE '+status)
    for name, valid in sorted(checks.items()): print('  '+name+': '+('PASS' if valid else 'FAIL'))
    print('  four real app parity: NOT_VERIFIED')
    print('  expert layout: NOT_VERIFIED')
    return 0 if status.startswith('PASS') else 1


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--hosted-url', default=None, help='Exact running viewport candidate URL')
    opts=ap.parse_args()
    raise SystemExit(run(opts.hosted_url))
