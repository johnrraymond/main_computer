"""Task Manager functional + diagnostic shadow test (isolated, disposable browser).

Runs the existing semantic Task Manager functional-parity campaign with
per-phase MCELDiagnosticPlugins evidence, plus a controlled polling-pressure
campaign. No real HTTP, process, service, AI, or schedule changes are allowed.
No speculative skip is executed and no rendering-equivalence claim is made.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_task_manager_functional_parity_frob import ROOT, FIXTURE_JS, browser_comparison

SCHEMA = 'mcel.task-manager.functional-shadow.v1'
PLUGIN = 'mcel-reactivity-shadow'
DEFAULT = Path('runtime/reports/mcel-task-manager-functional/report-shadow.json')
COUNTERS = ('executions', 'predictedSkips', 'possibleStateOnlySkips', 'contradictions',
            'observedMutations', 'propertyChanges', 'unverified', 'unknownBatches',
            'asyncMutations', 'unattributedMutations', 'incompleteScopes')

ENABLE_JS = '''async () => {
  const p=window.MCELDiagnosticPlugins;
  if(!p?.list().some(x=>x.id==='mcel-reactivity-shadow')) return {status:'missing-plugin'};
  p.configure('mcel-reactivity-shadow', {maxEvents:20000,maxNodesPerConsumer:256,sampleLayout:true});
  return p.enable('mcel-reactivity-shadow');
}'''


def summary(page) -> dict[str, Any]:
    raw = page.evaluate('''() => window.MCELReactivityShadowOracle?.report() || null''')
    apps = [a for a in (raw or {}).get('apps', []) if a.get('appId') == 'semantic-task-manager']
    app = apps[0] if len(apps) == 1 else {}
    stats = app.get('stats', {})
    producers=app.get('correlation',{}).get('producers',{})
    producer_counters={k:producers.get(k,0) for k in ('identified','unclassified','direct','scheduled',
        'unclassifiedNoPending','unclassifiedSinglePending','unclassifiedAmbiguousPending',
        'operationLinked','independentUi','unexplained')}
    return {'version': (raw or {}).get('oracleVersion'), 'sessions': len(apps),
            'notifications': app.get('notifications', 0),
            'consumers': app.get('consumers', 0),
            'counters': {k: stats.get(k, 0) for k in COUNTERS},
            'completeScopes': app.get('coverage', {}).get('completeScopes', 0),
            'totalScopes': app.get('coverage', {}).get('totalScoped', 0),
            'evictedEvents': app.get('eventsEvicted', 0),
            'producerCounters': producer_counters,
            'producerSources': {row['source']:row['records'] for row in producers.get('sources',[])},
            'producerUnclassifiedStructure':producers.get('unclassifiedStructure',{}),
            'producerPartitionValid':producers.get('partitionInvariant'),
            'producerClassificationValid':producers.get('classificationInvariant'),
            'unexplainedSemanticMarkers':producers.get('unclassifiedAttributeNames',{}),
            'revisionCount':len(app.get('correlation',{}).get('operations',[])),
            'receiptLinks':app.get('correlation',{}).get('async',{}).get('receiptMatches',0),
            'certified': (raw or {}).get('certified'),
            'productionSchedulerChanged': (raw or {}).get('productionSchedulerChanged'),
            'applicationOperationsInvoked': (raw or {}).get('applicationOperationsInvoked')}


class Collector:
    def __init__(self):
        self.stages = []
        self.errors = []
        self.plugin = None
        self.findings = []
        self.previous = {'notifications': 0, **{k: 0 for k in COUNTERS}}
        self.previous_producers = {}
        self.previous_sources = {}

    def __call__(self, page, phase: str):
        if phase == 'mounted':
            result = page.evaluate(ENABLE_JS)
            if result.get('status') != 'enabled':
                self.errors.append('PLUGIN_ENABLE_FAILED:' + str(result.get('status')))
                return
        if not self.errors:
            measured = summary(page)
            current = {'notifications': measured['notifications'], **measured['counters']}
            delta = {key: current[key] - self.previous[key] for key in current}
            producer_delta={k:n-self.previous_producers.get(k,0)
                for k,n in measured['producerCounters'].items()}
            source_delta={k:n-self.previous_sources.get(k,0)
                for k,n in measured['producerSources'].items()}
            self.stages.append({'phase': phase, **measured, 'delta': delta,
                'deltaProducers':producer_delta,'deltaSources':source_delta})
            self.previous = current
            self.previous_producers=measured['producerCounters']
            self.previous_sources=measured['producerSources']
        if phase == 'final':
            try:
                response = page.evaluate('''async () => await MCELDiagnosticPlugins.run('mcel-reactivity-shadow')''')
                self.plugin = response.get('result')
                self.findings = page.evaluate('''() => MCELDiagnosticPlugins.findings()
                    .filter(f=>f.pluginId==='mcel-reactivity-shadow')''')
                page.evaluate('''async () => await MCELDiagnosticPlugins.disable('mcel-reactivity-shadow')''')
            except Exception as exc:
                self.errors.append('PLUGIN_AUDIT_FAILED:' + type(exc).__name__)


def polling_pressure(repo: Path = ROOT) -> dict[str, Any]:
    """Verify the current 5-second polling controller before proposing replacement.

    Tick the existing timer under a delayed, in-memory HTTP response. This
    detects overlapping automatic requests and pause/resume regressions without
    waiting for wall-clock polling or sending any request to the host.
    """
    errors: list[str] = []
    steps: list[dict] = []
    with sync_playwright() as pw:
        browser, backend = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={'width': 1160, 'height': 880})
            page.set_default_timeout(8500)
            page.on('pageerror', lambda err: errors.append(str(err)))
            page.evaluate(FIXTURE_JS)
            # Wrap transport *before* the read port captures its fetch function.
            page.evaluate('''() => {
              const original=window.fetch;
              window.__tmPoll={requests:0,inflight:0,maxInflight:0,failNext:false};
              window.fetch=async (...args)=>{
                const url=String(args[0]);
                if(!url.endsWith('/api/applications/task/overview')) return original(...args);
                const m=window.__tmPoll; m.requests++; m.inflight++;
                m.maxInflight=Math.max(m.maxInflight,m.inflight);
                const fail=m.failNext; m.failNext=false;
                try {
                  await new Promise(done=>setTimeout(done,70));
                  if(fail) return {ok:false,status:503,json:async()=>({error:'isolated-network-outage'})};
                  return await original(...args);
                } finally {m.inflight--;}
              };
            }''')
            page.set_content(render_semantic_page('task-manager', repo, promoted=True))
            page.wait_for_function('''() => window.__mcelWorkbench?.app?.receipts()
              .some(r=>r.intentId==='intent:refresh' && r.disposition==='completed')''')
            page.wait_for_timeout(120)
            page.evaluate('''() => {window.__tmPoll.requests=0;window.__tmPoll.maxInflight=0;}''')
            enabled = page.evaluate(ENABLE_JS)
            if enabled.get('status') != 'enabled':
                raise RuntimeError('SHADOW_PLUGIN_NOT_ENABLED')
            timer = page.evaluate('''() => window.__taskIntervals.map(x=>x.ms)''')
            steps.append({'phase':'poll-registration','passed':timer == [5000], 'intervalsMs':timer})
            ticks = '''async n => {
              const tick=window.__taskIntervals.find(x=>x.ms===5000)?.fn;
              if(!tick) return {error:'NO_REFRESH_TIMER'};
              await Promise.all(Array.from({length:n},()=>tick()));
              return {...window.__tmPoll};
            }'''
            measured = page.evaluate(ticks, 4)
            steps.append({'phase':'overlap-guard','passed':measured['requests'] == 1 and measured['maxInflight'] == 1,
                          'requests':measured['requests'],'maxConcurrentRequests':measured['maxInflight']})
            button=page.locator('[data-mcel-refresh-toggle]')
            button.click()
            paused_before = page.evaluate('window.__tmPoll.requests')
            page.evaluate(ticks, 2)
            paused_after = page.evaluate('window.__tmPoll.requests')
            steps.append({'phase':'pause','passed':button.get_attribute('aria-pressed')=='false' and paused_before==paused_after,
                          'requestsBefore':paused_before,'requestsAfter':paused_after})
            button.click()
            page.evaluate(ticks, 1)
            resumed = page.evaluate('window.__tmPoll.requests')
            steps.append({'phase':'resume','passed':button.get_attribute('aria-pressed')=='true' and resumed==paused_after+1,
                          'requests':resumed})
            # Read failure must not leave the polling controller permanently busy.
            page.evaluate('window.__tmPoll.failNext=true')
            page.evaluate(ticks, 1)
            failed_count = page.evaluate('window.__tmPoll.requests')
            page.evaluate(ticks, 1)
            recovered = page.evaluate('''() => ({requests:window.__tmPoll.requests,
              latest:window.__mcelWorkbench.app.receipts().filter(r=>r.intentId==='intent:refresh').slice(-2).map(r=>r.disposition),
              mutations:window.__taskEffects.length})''')
            steps.append({'phase':'failed-refresh-recovers', 'passed':
                          recovered['requests']==failed_count+1 and
                          recovered['latest']==['failed','completed'] and recovered['mutations']==0,
                          'requestCount':recovered['requests'],'lastReceipts':recovered['latest']})
            plugin = page.evaluate('''() => MCELReactivityShadowOracle.report()''')
            app = next((x for x in plugin['apps'] if x['appId']=='semantic-task-manager'), {})
            revisions=app.get('correlation',{}).get('operations',[])
            monitorRevisions=[op for op in revisions if op.get('origin')=='monitor-poll']
            steps.append({'phase':'oracle-revision-correlation',
                          'passed':len(monitorRevisions)>=4 and all(op.get('revision',0)>0 for op in monitorRevisions),
                          'totalRevisions':len(revisions),'monitorPollRevisions':len(monitorRevisions)})
            producer=app.get('correlation',{}).get('producers',{})
            sources={s['source']:s for s in producer.get('sources',[])}
            steps.append({'phase':'oracle-producer-attribution',
                          'passed':producer.get('partitionInvariant') is True and
                            sources.get('monitor-toggle',{}).get('records',0)>=2 and
                            sources.get('monitor-settlement-status',{}).get('revisionLinked',0)>=1,
                          'identified':producer.get('identified',0),
                          'unclassified':producer.get('unclassified',0),
                          'unclassifiedStructure':producer.get('unclassifiedStructure',{}),
                          'monitorStatusExactRevisionRecords':sources.get('monitor-settlement-status',{}).get('revisionLinked',0),
                          'monitorToggleRecords':sources.get('monitor-toggle',{}).get('records',0)})
            steps.append({'phase':'oracle-after-polling','passed':app.get('notifications',0)>0 and
                          app.get('stats',{}).get('contradictions',0)==0,
                          'notifications':app.get('notifications',0),
                          'contradictions':app.get('stats',{}).get('contradictions')})
            page.evaluate('''async () => await MCELDiagnosticPlugins.disable('mcel-reactivity-shadow')''')
            if page.evaluate('window.__taskEffects.length') != 0:
                errors.append('POLLING_PERFORMED_CONSEQUENTIAL_ACTION')
            page.close()
        finally:
            browser.close()
    return {'status':'PASS' if steps and all(s['passed'] for s in steps) and not errors else 'FAIL',
            'steps':steps,'errors':errors,'backend':backend,
            'requestBoundary':'in-memory-fetch-only', 'refreshController':'incumbent-5000ms-busy-guard'}


def evaluate(plugin: dict | None, stages: list[dict], browser: dict, collector_errors: list[str],
             pressure: dict) -> dict:
    failures = list(collector_errors)
    apps = [a for a in (plugin or {}).get('apps',[]) if a.get('appId')=='semantic-task-manager']
    if (plugin or {}).get('oracleVersion')!=2: failures.append('ORACLE_VERSION_MISMATCH')
    if len(apps)!=1: failures.append('EXPECTED_ONE_TASK_MANAGER_SESSION')
    if (plugin or {}).get('productionSchedulerChanged') is not False: failures.append('PRODUCTION_SCHEDULER_CHANGED')
    if (plugin or {}).get('applicationOperationsInvoked') != 0: failures.append('SHADOW_INVOKED_OPERATION')
    if (plugin or {}).get('certified') is not False: failures.append('FALSE_SCHEDULING_CERTIFICATION')
    if browser.get('status') != 'PASS': failures.append('TASK_MANAGER_FUNCTIONAL_CAMPAIGN_FAILED')
    if pressure.get('status') != 'PASS': failures.append('POLLING_CONTROLLER_FUNCTIONAL_FAILURE')
    if len(stages) < 5: failures.append('INSUFFICIENT_SHADOW_STAGES')
    if stages and stages[-1]['notifications']==0: failures.append('NO_ORACLE_NOTIFICATIONS')
    if not all(s['delta']['notifications']>=0 for s in stages): failures.append('NOTIFICATION_COUNTER_REGRESSION')
    if not any(s['delta']['notifications']>0 for s in stages[1:]): failures.append('NO_PHASE_DEPENDENT_OBSERVATIONS')
    stats = apps[0].get('stats',{}) if len(apps)==1 else {}
    if stats.get('contradictions',0): failures.append('PREDICTED_SKIP_CONTRADICTION')
    if stats.get('executions',0)==0: failures.append('NO_CONSUMER_EXECUTIONS')
    if apps and not apps[0].get('mutationSupported'): failures.append('MUTATION_OBSERVER_UNAVAILABLE')
    correlation=apps[0].get('correlation',{}) if apps else {}
    if correlation.get('schema')!='mcel.reactivity.revision-correlation.v1' or not correlation.get('operations'):
        failures.append('REVISION_CORRELATION_NOT_COLLECTING')
    producers=correlation.get('producers',{})
    if apps and (not producers or producers.get('partitionInvariant') is not True):
        failures.append('PRODUCER_PARTITION_INVARIANT_FAILED')
    if apps and producers.get('classificationInvariant') is not True:
        failures.append('CAUSAL_CLASSIFICATION_PARTITION_FAILED')
    metrics = {'notifications': apps[0].get('notifications',0),
               'consumers':apps[0].get('consumers',0), **{k:stats.get(k,0) for k in COUNTERS},
               'completeScopes': apps[0].get('coverage',{}).get('completeScopes',0),
               'totalScoped':apps[0].get('coverage',{}).get('totalScoped',0),
               'eventsEvicted':apps[0].get('eventsEvicted',0),
               'operationRevisions':len(correlation.get('operations',[])),
               'bootstrapBatches':stats.get('bootstrapBatches',0),
               'operationsEvicted':correlation.get('operationsEvicted',0),
               'receiptLinks':correlation.get('async',{}).get('receiptMatches',0),
               'asyncTemporalCandidates':correlation.get('async',{}).get('singlePending',0),
               'asyncAmbiguous':correlation.get('async',{}).get('ambiguousPending',0),
               'asyncUnassociated':correlation.get('async',{}).get('noPending',0),
               'producerAttributed':correlation.get('producers',{}).get('identified',0),
               'producerUnclassified':correlation.get('producers',{}).get('unclassified',0),
               'operationLinkedDom':producers.get('operationLinked',0),
               'independentUiDom':producers.get('independentUi',0),
               'unexplainedDom':producers.get('unexplained',0),
               'unexplainedSemanticMarkerNames':producers.get('unclassifiedAttributeNames',{}),
               'producerDirect':correlation.get('producers',{}).get('direct',0),
               'producerScheduled':correlation.get('producers',{}).get('scheduled',0),
               'producerUnclassifiedNoPending':correlation.get('producers',{}).get('unclassifiedNoPending',0),
               'producerUnclassifiedSinglePending':correlation.get('producers',{}).get('unclassifiedSinglePending',0),
               'producerUnclassifiedAmbiguousPending':correlation.get('producers',{}).get('unclassifiedAmbiguousPending',0)} if apps else {}
    blockers = list(apps[0].get('coverage',{}).get('unresolved',[])) if apps else []
    blockers.append('temporal-attribution-is-not-causal-proof')
    if correlation.get('operationsEvicted',0): blockers.append('revision-history-evicted')
    if stats.get('unverified',0): blockers.append('unverified-consumer-dependencies')
    if stats.get('unknownBatches',0): blockers.append('unknown-state-batches')
    if metrics.get('producerUnclassified',0): blockers.append('unclassified-external-async-mutations')
    if metrics.get('producerAttributed',0): blockers.append('dom-producer-is-not-network-causality')
    if stats.get('incompleteScopes',0): blockers.append('incomplete-property-scopes')
    if apps and apps[0].get('eventsEvicted',0): blockers.append('detailed-event-buffer-evictions')
    return {'status':'FAIL' if failures else 'PASS_OBSERVATION', 'failures':sorted(set(failures)),
            'coverageStatus':'INCOMPLETE', 'coverageBlockers':sorted(set(blockers)),
            'selectiveSchedulingCertified':False, 'renderingEquivalenceTested':False,
            'metrics':metrics, 'interpretation':'Incumbent functional behavior and shadow evidence, not safe-skip certification'}


def run(repo: Path=ROOT, *, pressure: bool=True) -> dict:
    collector=Collector()
    browser=browser_comparison(repo,observer=collector,promoted=True)
    polling=polling_pressure(repo) if pressure else {'status':'NOT_RUN','steps':[]}
    result={'schema':SCHEMA, 'generatedAt':datetime.now(timezone.utc).isoformat(),
            'appId':'semantic-task-manager','source':'promoted Task Manager semantic runtime with in-memory backend',
            'functional':{'status':browser.get('status'),'scenarioCount':len(browser.get('cases',[])),
                          'cases':browser.get('cases',[]),'pageErrors':browser.get('pageErrors',[]),
                          'mockedEffectsOnly':True},
            'refresh':polling,'stages':collector.stages, 'diagnosticFindings':collector.findings,
            'plugin':collector.plugin,
            'authority':{'hostMutations':0,'productionSchedulerChanged':False}}
    result['evaluation']=evaluate(collector.plugin,collector.stages,browser,
                                 collector.errors,polling)
    return result


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    parser.add_argument('--timeout',type=int,default=120,help='reserved browser scenario limit in seconds')
    args=parser.parse_args(argv)
    if not 10 <= args.timeout <= 900: parser.error('--timeout must be 10..900')
    destination=args.output if args.output.is_absolute() else ROOT/args.output
    try:
        report=run()
    except Exception as exc:
        report={'schema':SCHEMA,'evaluation':{'status':'FAIL','coverageStatus':'INCOMPLETE',
                'selectiveSchedulingCertified':False,'failures':[type(exc).__name__+':'+str(exc)[:350]]}}
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    for stage in report.get('stages',[]):
        delta=stage['delta']
        print('TASK_SHADOW_PHASE',stage['phase'],
              'notifications='+str(delta['notifications']),
              'predictedSkips='+str(delta['predictedSkips']),
              'domMutations='+str(delta['observedMutations']),
              'propertyChanges='+str(delta['propertyChanges']),
              'contradictions='+str(delta['contradictions']),
              'revisions='+str(stage.get('revisionCount',0)),
              'receiptLinks='+str(stage.get('receiptLinks',0)),
              'producerAttributed='+str(stage['deltaProducers'].get('identified',0)),
              'producerUnclassified='+str(stage['deltaProducers'].get('unclassified',0)),
              'operationLinked='+str(stage['deltaProducers'].get('operationLinked',0)),
              'independentUi='+str(stage['deltaProducers'].get('independentUi',0)),
              'unexplained='+str(stage['deltaProducers'].get('unexplained',0)))
    result=report['evaluation']
    print('TASK_FUNCTIONAL_SHADOW',result['status'],'coverage='+result['coverageStatus'],
          'renderingEquivalence=NOT_TESTED','report:',destination)
    if result['failures']: print('TASK_SHADOW_FAILURES',result['failures'])
    return 0 if result['status']=='PASS_OBSERVATION' else 1


if __name__=='__main__':raise SystemExit(main())
