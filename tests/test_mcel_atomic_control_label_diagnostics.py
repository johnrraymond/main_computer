"""Browser proof for the split-label diagnostic blind spot and read-only repair evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'

HTML = '''
<style>
* { box-sizing: border-box; }
#calculator-app { width: 820px; height: 530px; display: grid; }
.calculator-shell { width: 810px; height: 510px; display: grid; }
.calculator-workspace { width: 780px; height: 480px; display: grid; align-content: start; }
.calculator-scientific-functions { width: 186px; display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-content: start; }
.calculator-scientific-functions button { min-width: 0; height: 51px; padding: 8px;
  font: 800 13.3333px/16px Arial; white-space: normal; overflow-wrap: anywhere;
  overflow: hidden; }
</style>
<div id="calculator-app"><div class="calculator-shell">
<div class="calculator-workspace" data-mcel-layout-zone="workspace">
  <div id="calculator-display">0</div>
  <div class="calculator-scientific-functions">
    <button data-mc-widget-id="calculator.graphing-token-comma">comma</button>
    <button data-mc-widget-id="calculator.graphing-function-round">round</button>
    <button data-mc-widget-id="calculator.graphing-function-sin">sin</button>
    <button id="normal-multiline">sin cos</button>
    <button id="explicitly-permitted" data-mcel-text-fit="allow-intraword-wrap">longerword</button>
    <button id="new-semantic-control">process</button>
  </div>
</div></div></div>
'''


def _browser():
    sync_api = pytest.importorskip('playwright.sync_api')
    executable = (os.getenv('MCEL_CHROMIUM_EXECUTABLE') or os.getenv('CHROMIUM_EXECUTABLE')
                  or shutil.which('chromium') or shutil.which('chromium-browser'))
    with sync_api.sync_playwright() as p:
        kwargs = {'headless': True}
        if executable:
            kwargs.update({'executable_path': executable, 'args': ['--no-sandbox', '--disable-dev-shm-usage']})
        browser = p.chromium.launch(**kwargs)
        try:
            yield browser
        finally:
            browser.close()


def test_split_words_fail_visual_fit_with_repair_concepts_and_no_dom_mutation():
    for browser in _browser():
        page = browser.new_page(viewport={'width': 1000, 'height': 900})
        page.set_content(HTML)
        page.add_script_tag(path=str(SCRIPTS / 'mcel-self-diagnosis.js'))
        page.add_script_tag(path=str(SCRIPTS / 'mcel-app-surface-conformance.js'))
        evidence = page.evaluate('''() => {
          const root = document.querySelector('#calculator-app');
          const before = root.outerHTML;
          const cssBefore = document.querySelector('style').textContent;
          const report = window.McelSelfDiagnosis.diagnose('calculator');
          const violations = report.measurements.visualIntegrityViolations;
          const split = violations.filter(item => item.type === 'atomic-control-label-split');
          const conformance = report.appSurfaceConformance;
          const fit = conformance.layers.find(item => item.id === 'runtime-visual-fit');
          const finding = report.findings.find(item => item.code === 'visual-integrity-violation');
          const after = root.outerHTML;
          return {
            status: report.verdict, split, fit: fit.status,
            conformance: conformance.status,
            findingTypes: finding?.evidence?.visualIntegrityViolations?.map(v=>v.type),
            currentLayoutIssues: report.buckets.activeLayoutIssues.map(i=>i.code),
            unchanged: before === after && cssBefore === document.querySelector('style').textContent
          };
        }''')
        assert evidence['unchanged'] is True, 'Diagnosis must not repair the DOM or CSS'
        assert evidence['status'] == 'fail'
        assert evidence['fit'] == 'fail'
        assert evidence['conformance'] == 'fail'
        assert 'visual-integrity-violation' in evidence['currentLayoutIssues']
        assert 'atomic-control-label-split' in evidence['findingTypes']
        by_id = {finding['controlId']: finding for finding in evidence['split']}
        for control_id, label in [('calculator.graphing-token-comma', 'comma'),
                                  ('calculator.graphing-function-round', 'round')]:
            pieces = by_id[control_id]['fragments'][0]['fragments']
            assert len(pieces) >= 2
            assert ''.join(pieces) == label
        assert 'new-semantic-control' in by_id  # Same detector handles newly named controls.
        assert 'normal-multiline' not in by_id
        assert 'explicitly-permitted' not in by_id
        for finding in by_id.values():
            assert finding['css']['overflowWrap'] == 'anywhere'
            assert finding['geometry']['contentWidth'] < finding['geometry']['buttonWidth']
            assert len(finding['repairConcepts']) == 3
            assert all(c['applicationAuthorized'] is False for c in finding['repairConcepts'])
            assert all(c['status'] == 'proposed-unverified' for c in finding['repairConcepts'])


def test_resizing_grid_eliminates_split_finding_without_diagnostic_css_edits():
    for browser in _browser():
        page = browser.new_page(viewport={'width': 1200, 'height': 900})
        page.set_content(HTML)
        page.add_script_tag(path=str(SCRIPTS / 'mcel-self-diagnosis.js'))
        raw = page.evaluate('''() => {
          const root = document.querySelector('#calculator-app');
          const f = window.McelSelfDiagnosis._private.detectAtomicControlLabelSplits;
          const narrow = f(root, {appId:'calculator'}).length;
          root.querySelector('.calculator-scientific-functions').style.width='390px';
          const wide = f(root, {appId:'calculator'}).length;
          return {narrow, wide};
        }''')
        assert raw['narrow'] >= 3
        assert raw['wide'] == 0


def test_browser_range_coverage_but_no_overflow_still_classifies_split_label():
    for browser in _browser():
        page = browser.new_page(viewport={'width': 1000, 'height': 900})
        page.set_content(HTML)
        page.add_script_tag(path=str(SCRIPTS / 'mcel-self-diagnosis.js'))
        result = page.evaluate('''() => {
          const root=document.querySelector('#calculator-app');
          const api=window.McelSelfDiagnosis._private;
          const button=root.querySelector('[data-mc-widget-id="calculator.graphing-token-comma"]');
          return {
            readable: api.collectReadableTextBoxes(root,{appId:'calculator'})
               .filter(x => x.el===button).length,
            scrollWidth: button.scrollWidth,
            clientWidth: button.clientWidth,
            violations: api.detectVisualIntegrityViolations(root,{appId:'calculator'})
               .filter(x=>x.type==='atomic-control-label-split').length
          };
        }''')
        assert result['readable'] >= 2
        assert result['scrollWidth'] == result['clientWidth']
        assert result['violations'] >= 3


def test_reviewed_diagnostic_concepts_flow_to_existing_lab_repair_export(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required to verify the MCEL Lab repair export')
    script = tmp_path / 'repair-export.cjs'
    script.write_text('''
const registry = require(process.argv[2]);
const toolkit = require(process.argv[3]);
const adapter = require(process.argv[4]);
(async () => {
  adapter.setRuntimeBindings({
    validateBlueprintContract: () => ({ok:true, appId:'calculator', aspectId:'layout',
      findings:[{id:'wrap-1', code:'visual-integrity-violation',
        evidence:{visualIntegrityViolations:[{
          type:'atomic-control-label-split', selector:'button.example', controlId:'control-1', label:'round',
          repairConcepts:[{id:'derive-control-intrinsic-minimum', hypothesis:'Insufficient intrinsic width',
            proposedChangeType:'intrinsic-control-sizing', requiredChecks:['word fits'] }]
        }]}}]}),
    exportRepairContext: payload => ({ok:true, appId:payload.appId, aspectId:payload.aspectId,
       reviewedFindingIds:payload.reviewedFindingIds})
  });
  await adapter.executeIntent('validateBlueprintContract',{appId:'calculator',aspectId:'layout'});
  const unreviewed = await adapter.executeIntent('exportRepairContext',{
    appId:'calculator',aspectId:'layout',reviewedFindingIds:['something-else']});
  const absent = adapter.getState().repairContext.proposedRepairConcepts;
  const reviewed = await adapter.executeIntent('exportRepairContext',{
    appId:'calculator',aspectId:'layout',reviewedFindingIds:['wrap-1']});
  const state = adapter.getState();
  console.log(JSON.stringify({statuses:[unreviewed.status,reviewed.status],absent,
    concepts:state.repairContext.proposedRepairConcepts,
    boundary:state.repairContext.patchApplicationBoundary,
    authorized:state.repairContext.applicationAuthorized}));
})().catch(e=>{console.error(e);process.exit(1);});
''', encoding='utf-8', newline='\n')
    args = [node, str(script), str(SCRIPTS / 'mcel-domain-adapter-registry.js'),
            str(SCRIPTS / 'mcel-semantic-adapter-toolkit.js'),
            str(SCRIPTS / 'mcel-lab-semantic-adapter.js')]
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result['statuses'] == ['pass', 'pass']
    assert result['absent'] == []
    assert len(result['concepts']) == 1
    assert result['concepts'][0]['findingId'] == 'wrap-1'
    assert result['concepts'][0]['status'] == 'proposed-unverified'
    assert result['concepts'][0]['applicationAuthorized'] is False
    assert result['authorized'] is False
    assert result['boundary'] == 'external-new-patch-workflow'


def test_existing_counter_reports_split_as_active_error():
    for browser in _browser():
        page = browser.new_page(viewport={'width': 1000, 'height': 900})
        page.set_content(HTML)
        page.add_script_tag(path=str(SCRIPTS / 'mcel-self-diagnosis.js'))
        page.add_script_tag(path=str(SCRIPTS / 'mcel-app-surface-conformance.js'))
        page.add_script_tag(path=str(SCRIPTS / 'mcel-diagnostics-counter-widget.js'))
        observed = page.evaluate('''() => {
          const api = window.MCELDiagnosticsCounterWidget;
          const first = api.refresh('calculator');
          // The existing widget intentionally downgrades startup issues for 5s.
          first.history.pageStartedAt = new Date(Date.now() - 10000).toISOString();
          const record = api.refresh('calculator');
          const payload = api._private.compactPayload(record.report,record.counts,record.history);
          const widget=document.querySelector('#calculator-diagnostics-counter');
          return {state:widget.dataset.state, counts:payload.counts,
            verdict:payload.verdict, current:payload.current.issues.map(i=>i.code),
            visual:payload.measurements.visualIntegrityViolations?.map(v=>v.type),
            fit:payload.appSurfaceConformance?.layers?.find(l=>l.id==='runtime-visual-fit')?.status};
        }''')
        assert observed['state'] == 'error'
        assert observed['counts']['errors'] >= 1
        assert observed['verdict'] == 'fail'
        assert 'visual-integrity-violation' in observed['current']
        assert 'atomic-control-label-split' in observed['visual']
        assert observed['fit'] == 'fail'
