"""Read-only, mocked SLH shadow-reactivity oracle capture.

Run from repository root:
    python -m tools.mcel_reactivity_shadow_oracle_frob

Uses the existing browser diagnostic plugin and the same mocked SLH ports as
UI acceptance tests. Never contacts LMS, Coolify or a live SEH.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from tests.test_mcel_seh_two_action_lifecycle_ui import _setup

DEFAULT_OUTPUT = Path('runtime/reports/mcel-reactivity-shadow/mocked-slh-shadow-0001.json')


def run_mocked_shadow() -> dict:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page, notebook = _setup(browser, 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            startup = page.evaluate("async()=>await MCELDiagnosticPlugins.enable('mcel-reactivity-shadow')")
            if startup.get('status') != 'enabled':
                raise RuntimeError('Oracle plugin did not enable: ' + repr(startup))
            field = notebook.locator('[data-mcel-operation-field="deploymentStatus"]')
            observations = []
            for expected in ('PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED', 'SEH_UNDEPLOYED',
                             'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'):
                page.evaluate('(state)=>{window.sehUiPhase=state}', expected)
                receipt = page.evaluate("async()=>await __mcelSlh.app.execute('intent:read-seh-deployment')")
                actual = field.inner_text()
                observations.append({'expected':expected,'actual':actual,
                                     'readCompleted':receipt.get('disposition') == 'completed'})
            plugin_result = page.evaluate("async()=>(await MCELDiagnosticPlugins.run('mcel-reactivity-shadow')).result")
            findings = page.evaluate("MCELDiagnosticPlugins.findings().filter(f=>f.pluginId==='mcel-reactivity-shadow')")
            # Phase-2 shadow acceptance checks: evidence must cover external
            # notification paths and property writes without changing the UI.
            assert plugin_result['oracleVersion'] == 2, plugin_result
            apps = plugin_result['apps']
            slh = next((app for app in apps if app['appId'] == 'slh'), None)
            assert slh and slh['notifications'] >= 3, apps
            assert slh['stats']['executions'] > 0, slh
            assert slh['stats']['contradictions'] == 0, slh
            assert slh['coverage']['completeScopes'] > 0, slh
            assert 'async-commit' in slh['coverage']['invalidations'], slh
            assert 'color' in slh['coverage']['trackedKinds'], slh
            assert plugin_result['productionSchedulerChanged'] is False
            assert plugin_result['applicationOperationsInvoked'] == 0
            disabled = page.evaluate("async()=>await MCELDiagnosticPlugins.disable('mcel-reactivity-shadow')")
            if errors:
                raise RuntimeError('Browser errors: ' + repr(errors))
            if any(o['expected'] != o['actual'] or not o['readCompleted'] for o in observations):
                raise RuntimeError('Incumbent SLH behavior regressed: ' + repr(observations))
            return {
                'schema':'mcel.reactivity.shadow.mocked-harness.v1',
                'generatedAt':datetime.now(timezone.utc).isoformat(),
                'source':'mocked-browser-fixture-no-real-services',
                'incumbentUIStable':True,
                'oracleEnabled':startup['status'] == 'enabled',
                'oracleDisabled':disabled['status'] == 'disabled',
                'certified':False,
                'coverageChecks':{
                    'observedExternalInvalidation':True,
                    'propertyScopeCapture':True,
                    'colorRefreshObserved':True,
                    'noSchedulerChanges':True,
                    'noContradictionsInMockedRun':True,
                },
                'observations':observations,
                'plugin':plugin_result,
                'diagnosticFindings':findings,
            }
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT), help='JSON evidence destination')
    args = parser.parse_args()
    report=run_mocked_shadow()
    dest=Path(args.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('MCEL_REACTIVITY_SHADOW_MOCKED_OBSERVATION',
          'COMPLETE_UNCERTIFIED',report['plugin']['status'], 'report:',dest)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
