"""SLH functional shadow lane: incumbent browser actions + diagnostic-plugin evidence.

This is a disposable, browser-mocked companion to the SLH functional battery.
It is not a real-host lifecycle test or selective-renderer certification.
Run: python -m tools.mcel_slh_functional_shadow --output runtime/reports/mcel-slh-full-functional/shadow.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import traceback

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_slh_lifecycle_disable_enablement_oracle import MOCK

PLUGIN_ID = 'mcel-reactivity-shadow'
SCHEMA = 'mcel.slh.functional-shadow.v1'
DEFAULT_OUTPUT = Path('runtime/reports/mcel-slh-full-functional/shadow.json')
COUNTERS = ('executions', 'predictedSkips', 'possibleStateOnlySkips', 'contradictions',
            'observedMutations', 'propertyChanges', 'unverified', 'unknownBatches',
            'asyncMutations', 'unattributedMutations', 'incompleteScopes')


def evaluate_shadow(raw: dict, stages: list[dict], browser_errors: list[str], actions: list[str]) -> dict:
    """Classify measurement reliability independently of functional correctness.

    Incomplete dependency coverage is not a failed functional test, nor proof
    that any predicted skip is safe. Missing observations or contradictions ARE
    failures of this diagnostic lane.
    """
    apps = [a for a in raw.get('apps', []) if a.get('appId') == 'slh']
    failures = []
    if raw.get('oracleVersion') != 2:
        failures.append('SHADOW_VERSION_UNEXPECTED')
    if raw.get('productionSchedulerChanged') is not False or raw.get('applicationOperationsInvoked') != 0:
        failures.append('SHADOW_AUTHORITY_VIOLATION')
    if raw.get('certified') is not False:
        failures.append('UNSUPPORTED_CERTIFICATION_CLAIM')
    if len(apps) != 1:
        failures.append('EXPECTED_ONE_SLH_SESSION')
    if browser_errors:
        failures.append('BROWSER_PAGE_ERRORS')
    if actions != ['toggle', 'toggle', 'toggle-enablement', 'toggle-enablement']:
        failures.append('LIFECYCLE_ACTION_MISMATCH')
    if [stage.get('phase') for stage in stages] != [
            'seed-running', 'stop', 'start', 'disable', 'enable']:
        failures.append('LIFECYCLE_PHASE_COVERAGE_INCOMPLETE')
    if not all(stage.get('functionalPass') for stage in stages):
        failures.append('UI_STATE_MISMATCH')
    for stage in stages:
        if stage.get('phase') in ('stop', 'start', 'disable', 'enable') and stage.get('deltaNotifications', 0) <= 0:
            failures.append('NO_OBSERVATIONS_DURING_' + stage['phase'].upper())
    metrics = {}
    blockers = []
    if len(apps) == 1:
        app = apps[0]
        metrics = {'notifications': app.get('notifications', 0),
                   'consumers': app.get('consumers', 0),
                   **{k: app.get('stats', {}).get(k, 0) for k in COUNTERS},
                   'completeScopes': app.get('coverage', {}).get('completeScopes', 0),
                   'totalScoped': app.get('coverage', {}).get('totalScoped', 0),
                   'eventsEvicted': app.get('eventsEvicted', 0)}
        correlation=app.get('correlation',{})
        if correlation.get('schema')!='mcel.reactivity.revision-correlation.v1' or not correlation.get('operations'):
            failures.append('OPERATION_REVISION_EVIDENCE_MISSING')
        if not any(x.get('receipts',0)>0 for x in correlation.get('operations',[])):
            failures.append('REVISION_RECEIPT_LINK_MISSING')
        metrics['operationRevisions']=len(correlation.get('operations',[]))
        metrics['bootstrapBatches']=app.get('stats',{}).get('bootstrapBatches',0)
        metrics['operationsEvicted']=correlation.get('operationsEvicted',0)
        metrics['receiptLinks']=correlation.get('async',{}).get('receiptMatches',0)
        metrics['asyncTemporalCandidates']=correlation.get('async',{}).get('singlePending',0)
        metrics['asyncAmbiguous']=correlation.get('async',{}).get('ambiguousPending',0)
        metrics['asyncUnassociated']=correlation.get('async',{}).get('noPending',0)
        producers=correlation.get('producers',{})
        metrics['producerAttributed']=producers.get('identified',0)
        metrics['producerUnclassified']=producers.get('unclassified',0)
        metrics['operationLinkedDom']=producers.get('operationLinked',0)
        metrics['independentUiDom']=producers.get('independentUi',0)
        metrics['unexplainedDom']=producers.get('unexplained',0)
        metrics['unexplainedSemanticMarkerNames']=producers.get('unclassifiedAttributeNames',{})
        metrics['producerDirect']=producers.get('direct',0)
        metrics['producerScheduled']=producers.get('scheduled',0)
        metrics['producerUnclassifiedNoPending']=producers.get('unclassifiedNoPending',0)
        if producers.get('partitionInvariant') is not True:
            failures.append('PRODUCER_PARTITION_INVARIANT_FAILED')
        if producers.get('classificationInvariant') is not True:
            failures.append('CAUSAL_CLASSIFICATION_PARTITION_FAILED')
        if not app.get('mutationSupported'):
            failures.append('MUTATION_OBSERVER_UNAVAILABLE')
        if not metrics['notifications'] or not metrics['executions'] or not metrics['completeScopes']:
            failures.append('SHADOW_NOT_COLLECTING')
        if metrics['contradictions']:
            failures.append('PREDICTED_SKIP_CONTRADICTION')
        # Blockers remain explicit even when the diagnostic observation passes.
        blockers = list(app.get('coverage', {}).get('unresolved', []))
        blockers.append('temporal-attribution-is-not-causal-proof')
        if correlation.get('operationsEvicted',0): blockers.append('revision-history-evicted')
        if metrics['incompleteScopes']:
            blockers.append('incomplete-property-scopes')
        if metrics['unknownBatches']:
            blockers.append('unknown-state-batches')
        if metrics['producerUnclassified']:
            blockers.append('unclassified-external-async-mutations')
        if metrics['producerAttributed']:
            blockers.append('dom-producer-is-not-network-causality')
        if metrics['unverified']:
            blockers.append('unverified-consumer-dependencies')
        if metrics['eventsEvicted']:
            blockers.append('detailed-event-buffer-eviction')
    return {
        'status': 'FAIL' if failures else 'PASS_OBSERVATION',
        'coverageStatus': 'INCOMPLETE',
        'selectiveSchedulingCertified': False,
        'failures': sorted(set(failures)),
        'coverageBlockers': sorted(set(blockers)),
        'metrics': metrics,
        'interpretation': 'Functional UI transitions and oracle observation are tested; hypothetical skips are not authorized.'
    }


def run_mocked_functional_shadow() -> dict:
    """Exercise the actual SLH lifecycle UI with the oracle running in shadow."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.set_default_timeout(12000)
            errors: list[str] = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state === 'not_configured'")
            start = page.evaluate('''async () => {
              const api = window.MCELDiagnosticPlugins;
              if (!api?.list().some(x => x.id === 'mcel-reactivity-shadow'))
                return {status:'missing'};
              api.configure('mcel-reactivity-shadow',
                {maxEvents:20000, maxNodesPerConsumer:256, sampleLayout:true});
              return api.enable('mcel-reactivity-shadow');
            }''')
            if start.get('status') != 'enabled':
                raise RuntimeError('SHADOW_PLUGIN_ENABLE_FAILED:' + repr(start))
            form = page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            toggle = form.locator('button[data-intent="intent:toggle-organization"]')
            enablement = form.locator('button[data-intent="intent:toggle-organization-enablement"]')
            stages: list[dict] = []
            prior = 0
            previous_counters = {key:0 for key in COUNTERS}
            previous_producers = {'identified':0,'unclassified':0,'direct':0,'scheduled':0,
                                  'operationLinked':0,'independentUi':0,'unexplained':0}

            def record(phase: str, expected: str, toggle_text: str, enable_text: str,
                       toggle_enabled: bool, enable_enabled: bool) -> None:
                nonlocal prior, previous_counters, previous_producers
                page.wait_for_function("expected => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state === expected", arg=expected)
                # Truth is based on both canonical semantic state and actual UI.
                actual = page.evaluate("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state")
                ui = {'toggleLabel': toggle.inner_text().strip(),
                      'enablementLabel': enablement.inner_text().strip(),
                      'toggleEnabled': toggle.is_enabled(),
                      'enablementEnabled': enablement.is_enabled()}
                passed = actual == expected and ui == {
                    'toggleLabel':toggle_text, 'enablementLabel':enable_text,
                    'toggleEnabled':toggle_enabled, 'enablementEnabled':enable_enabled}
                apps = page.evaluate('''() => window.MCELReactivityShadowOracle.report().apps
                  .filter(a => a.appId === 'slh').map(a => ({
                     notifications:a.notifications, consumers:a.consumers,
                     stats:a.stats, correlation:a.correlation, coverage:{completeScopes:a.coverage.completeScopes,
                       totalScoped:a.coverage.totalScoped} }))''')
                app = apps[0] if len(apps) == 1 else {}
                count = app.get('notifications', 0)
                counts = {k:app.get('stats',{}).get(k,0) for k in COUNTERS}
                deltas = {k:counts[k]-previous_counters[k] for k in COUNTERS}
                previous_counters = counts
                producers=app.get('correlation',{}).get('producers',{})
                producer_counts={k:producers.get(k,0) for k in previous_producers}
                producer_deltas={k:producer_counts[k]-previous_producers[k] for k in producer_counts}
                previous_producers=producer_counts
                stages.append({'phase':phase, 'expectedState':expected, 'observedState':actual,
                               'ui':ui, 'functionalPass':passed,
                               'deltaNotifications': count-prior,
                               'notifications':count, 'consumers':app.get('consumers',0),
                               'revisionCount':len(app.get('correlation',{}).get('operations',[])),
                               'receiptLinks':app.get('correlation',{}).get('async',{}).get('receiptMatches',0),
                               'counters':counts, 'deltaCounters':deltas,
                               'producers':producer_counts,'deltaProducers':producer_deltas})
                prior = count
                if not passed:
                    raise AssertionError('INCUMBENT_UI_STATE_MISMATCH:' + repr(stages[-1]))

            # A functional failure must not discard the diagnostic evidence
            # collected up to that point. Always audit through the registry.
            scenario_error = None
            try:
                page.evaluate("() => {window.lifecycle = 'running'}")
                receipt = page.evaluate("async () => await window.__mcelSlh.app.execute('intent:refresh-lifecycle')")
                if receipt.get('disposition') != 'completed':
                    raise AssertionError('SEED_RUNNING_REFRESH_FAILED:' + repr(receipt))
                record('seed-running', 'running', 'Stop', 'Disable', True, True)

                toggle.click()
                record('stop', 'stopped', 'Start', 'Disable', True, True)
                toggle.click()
                record('start', 'running', 'Stop', 'Disable', True, True)
                enablement.click()
                record('disable', 'disabled', 'Start', 'Enable', False, True)
                enablement.click()
                record('enable', 'running', 'Stop', 'Disable', True, True)
            except Exception as exc:
                scenario_error = type(exc).__name__ + ': ' + str(exc)[:1200]

            actions = page.evaluate('''() => window.slhCalls
              .filter(c => c.url === '/api/applications/slh/setup/action'
                && c.body?.action !== 'authenticate')
              .map(c => c.body.action)''')
            response = page.evaluate("async () => await MCELDiagnosticPlugins.run('mcel-reactivity-shadow')")
            if response.get('status') != 'complete' or not isinstance(response.get('result'), dict):
                raise RuntimeError('SHADOW_AUDIT_UNAVAILABLE:' + repr(response))
            plugin = response['result']
            findings = page.evaluate("() => MCELDiagnosticPlugins.findings().filter(f => f.pluginId === 'mcel-reactivity-shadow')")
            outcome = evaluate_shadow(plugin, stages, errors, actions)
            if scenario_error:
                outcome['status'] = 'FAIL'
                outcome['failures'] = sorted(set(outcome['failures'] + ['FUNCTIONAL_SCENARIO_EXCEPTION']))
                outcome['scenarioError'] = scenario_error
            stopped = page.evaluate("async () => await MCELDiagnosticPlugins.disable('mcel-reactivity-shadow')")
            if stopped.get('status') != 'disabled':
                outcome['status'] = 'FAIL'
                outcome['failures'].append('PLUGIN_DISABLE_FAILED')
            return {
                'schema': SCHEMA, 'generatedAt': datetime.now(timezone.utc).isoformat(),
                'source': 'isolated-mocked-browser-slh-functional-actions',
                'oraclePlugin': PLUGIN_ID, 'oracleVersion': plugin.get('oracleVersion'),
                'functionalActions': actions, 'stages': stages,
                'evaluation': outcome, 'plugin': plugin,
                'diagnosticFindings': findings,
                'safety': {'productionSchedulerChanged':plugin.get('productionSchedulerChanged'),
                           'applicationOperationsInvoked':plugin.get('applicationOperationsInvoked'),
                           'backendCallsMocked':True, 'candidateRendererUsed':False},
            }
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = run_mocked_functional_shadow()
    except Exception as exc:
        report = {'schema':SCHEMA, 'generatedAt':datetime.now(timezone.utc).isoformat(),
                  'source':'isolated-mocked-browser-slh-functional-actions',
                  'evaluation':{'status':'FAIL','coverageStatus':'INCOMPLETE',
                                'selectiveSchedulingCertified':False,
                                'failures':['SCENARIO_EXCEPTION'], 'error':str(exc),
                                'tracebackTail':traceback.format_exc()[-2500:]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    outcome = report['evaluation']
    print('SLH_FUNCTIONAL_SHADOW', outcome['status'],
          'coverage='+outcome['coverageStatus'],
          'notifications='+str(outcome.get('metrics', {}).get('notifications',0)),
          'contradictions='+str(outcome.get('metrics', {}).get('contradictions',0)),
          'report='+str(args.output), flush=True)
    return 0 if outcome['status']=='PASS_OBSERVATION' else 1


if __name__ == '__main__':
    raise SystemExit(main())
