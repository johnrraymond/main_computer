#!/usr/bin/env python3
"""Cumulative MCEL functional acid test: source -> behavior -> Chromium -> PNG.

This is an inventory and evidence runner, NOT a replacement for MCEL's existing
canonical truth audit or proof authorities. A visual capture is never self-approved.
Task Manager production code is not imported, mutated or used as a test oracle.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.mcel_notebook_projection import compile_notebook_preview, render_notebook_html
from tools.mcel_element_behavior_audit import audit as audit_elements
from tools.mcel_element_runtime_probe import run_tab_witness

SCHEMA = 'mcel.functional-acid.v2'
# NOT_IMPLEMENTED is reserved for independently demonstrated absence of a
# promised capability. Lack of a witness is UNVERIFIED, never NOT_IMPLEMENTED.
STATUS = ('PASS', 'FAIL', 'UNVERIFIED', 'NOT_IMPLEMENTED')
REPORT_PATH = Path('runtime/reports/mcel-functional-acid')
SOURCE = Path('main_computer/web/applications/scripts')
STYLE = Path('main_computer/web/applications/styles/mcel-lab.css')
NOTEBOOK = Path('mcel_experiments/task-manager-notebook/application.js')
FIXTURE = Path('mcel_experiments/task-manager-notebook/read-only-sample.json')
TAB_SPECIMEN = Path('tools/fixtures/mcel_acid_tab_ir.json')
BROWSER_SCRIPTS = (
    'mcel-element-registry.js', 'mcel-elements-core.js',
    'mcel-toolkit-core.js', 'mcel-concern-core.js',
    'mcel-file-basket-model.js', 'mcel-file-basket-controller.js',
    'mcel-project-concern-workbench.js',
    'mcel-git-file-basket-treegrid-lab.js', 'mcel-element-acid-test.js',
)
# Selected existing independent test authorities. Full mode runs all test_mcel_*.py.
CORE_SUITES = {
    'dsl-and-ir': ('tests/test_mcel_dsl_compiler.py', 'tests/test_mcel_application_ir.py',
                   'tests/test_mcel_view_transition_composition.py'),
    'semantic-source-and-repair': ('tests/test_mcel_contract_guarantees.py',
                                   'tests/test_mcel_scm_serialization_repair.py'),
    'layout-and-fit': ('tests/test_mcel_shared_layout_grammar.py',
                       'tests/test_mcel_surface_fit_contract.py'),
    'operations-and-safety': ('tests/test_mcel_semantic_adapter_toolkit.py',
                              'tests/test_mcel_host_bound_semantic_admission.py'),
    'projection-and-interaction': ('tests/test_mcel_notebook_projection.py',),
    'element-behavior-audit': ('tests/test_mcel_element_behavior_audit.py', 'tests/test_mcel_core_toolkit_runtime_probe.py',
                               'tests/test_mcel_file_basket_runtime_probe.py'),
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result(name: str, status: str, *, details: str = '', evidence: Any = None) -> dict[str, Any]:
    assert status in STATUS
    item = {'id': name, 'status': status, 'details': details}
    if evidence is not None:
        item['evidence'] = evidence
    return item


def _run_pytest(repo: Path, selectors: list[str], timeout: int) -> dict[str, Any]:
    if not selectors:
        return result('pytest', 'UNVERIFIED', details='No executable selectors registered')
    missing = [x for x in selectors if not (repo / x).is_file()]
    if missing:
        return result('pytest', 'FAIL', details='Required test selectors missing', evidence=missing)
    cmd = [sys.executable, '-m', 'pytest', '-q', '-rA', *selectors]
    try:
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result('pytest', 'FAIL', details=str(exc), evidence={'selectors': selectors})
    stdout = proc.stdout[-3500:]
    # Tests can be skipped under certain OS capabilities. Do not call a
    # completely skipped suite PASS merely because pytest returned zero.
    all_skipped = ' passed' not in stdout and ' skipped' in stdout
    # Pytest failing before collection (e.g. an incomplete export causing an
    # autouse build hook to abort) is not a failed MCEL behavioral assertion.
    # Record the infrastructure error explicitly; the suite remains UNVERIFIED.
    infrastructure_error = proc.returncode != 0 and any(
        token in proc.stdout + proc.stderr for token in
        ('INTERNALERROR>', 'ERROR: usage:', 'ERROR: file or directory not found:'))
    status = ('UNVERIFIED' if infrastructure_error or all_skipped else
              'PASS' if proc.returncode == 0 else 'FAIL')
    return result('pytest', status,
                  details=f'exit={proc.returncode}; classification=' +
                      ('infrastructure-error' if infrastructure_error else
                       'all-skipped' if all_skipped else
                       'executed-test-observation'),
                  evidence={'selectors': selectors, 'stdoutTail': stdout,
                            'stderrTail': proc.stderr[-1500:],
                            'executionClassification': 'infrastructure-error' if infrastructure_error
                                                   else 'assertion-or-success'})


def _run_browser(repo: Path, output: Path, baseline: Path | None,
                 *, approve: bool, chromium: str | None, full_elements: bool=False) -> dict[str, Any]:
    """Real Chromium DOM, event and geometry tests; PNGs are separate review evidence."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'status': 'UNVERIFIED', 'error': 'Playwright not installed',
                'elements': [], 'checks': [], 'visuals': []}
    from PIL import Image, ImageChops
    scripts = {}
    for name in BROWSER_SCRIPTS:
        path = repo / SOURCE / name
        if not path.is_file():
            return {'status': 'FAIL', 'error': f'Required MCEL source missing: {path}',
                    'elements': [], 'checks': [], 'visuals': []}
        scripts[name] = path.read_text(encoding='utf-8')
    # Task Manager is a *frozen specimen*, not a dependency of MCEL's element
    # library. Missing exported specimens must be visible as gaps but cannot
    # suppress independent registry, composition, and Chromium layout tests.
    checks, visuals, elements = [], [], []
    runtime_witness = {}
    missing_specimen = [p.as_posix() for p in (NOTEBOOK, FIXTURE)
                        if not (repo / p).is_file()]
    html = None
    if missing_specimen:
        checks.append(result('notebook-specimen', 'UNVERIFIED',
            details='Frozen, optional notebook source/fixture missing from repository snapshot',
            evidence={'missingPaths': missing_specimen}))
    else:
        try:
            html = compile_notebook_preview(repo / NOTEBOOK, fixture_path=repo / FIXTURE)
        except (ValueError, OSError) as exc:
            checks.append(result('notebook-specimen', 'FAIL',
                details=f'Present notebook specimen failed compilation: {type(exc).__name__}: {exc}'))
        else:
            checks.append(result('notebook-specimen', 'PASS',
                details='Compiled frozen notebook through canonical MCEL projection'))
    # Every script is the actual repository library code, not a reimplementation.
    output.mkdir(parents=True, exist_ok=True)
    snapshots = output / 'screenshots'
    snapshots.mkdir(exist_ok=True)
    if approve and baseline is None:
        raise ValueError('--approve-baselines requires --baseline-dir')
    errors = []
    try:
        with sync_playwright() as pw:
            launch = {'headless': True}
            if chromium:
                launch['executable_path'] = chromium
                launch['args'] = ['--no-sandbox']
            else:
                exe = shutil.which('chromium') or shutil.which('chromium-browser')
                if exe:
                    launch['executable_path'] = exe
                    launch['args'] = ['--no-sandbox']
            browser = pw.chromium.launch(**launch)
            try:
                # Registry + reusable element showcase: the inventory grows
                # automatically when a new MCEL element is registered.
                page = browser.new_page(viewport={'width': 1280, 'height': 900},
                                        device_scale_factor=1, reduced_motion='reduce')
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.set_content('<!doctype html><html><head><style>' +
                    (repo / STYLE).read_text(encoding='utf-8') +
                    '</style></head><body><div id="mcel-element-acid-summary"></div>' +
                    '<div id="mcel-element-acid-report"></div>' +
                    '<main id="mcel-element-acid-canvas"></main></body></html>')
                for name, src in scripts.items():
                    page.add_script_tag(content=src)
                evidence = page.evaluate('''() => {
                    const defs = window.McelElementRegistry.all();
                    const out = window.McelElementAcidTest.run();
                    return {defs,acidIds:window.McelElementAcidTest.acidElementIds,
                        acid:out, observed:[...document.querySelectorAll('[data-mcel-element-demo-role="catalog-card"]')]
                        .map(el => ({id:el.getAttribute('data-mcel-element'),
                             label:el.querySelector('h6')?.textContent || '',
                             width:el.getBoundingClientRect().width,
                             height:el.getBoundingClientRect().height,
                             visible:el.getClientRects().length>0})),
                        duplicateIds:[...new Set(defs.map(d=>d.id).filter((id,i,a)=>a.indexOf(id)!==i))]
                    };
                }''')
                audit = audit_elements(repo, evidence['defs'])
                acid_ids = set(evidence['acidIds'])
                cards = {x['id']: x for x in evidence['observed']}
                contract_audit = page.evaluate('''() => window.McelElementRegistry.all().map(d => {
                    const e=window.McelElementRegistry.serializeElement(d.id, {id:'acid-candidate'});
                    return {id:d.id,ok:e.elementId===d.id && e.kind===d.kind &&
                      e.family===d.family && e.id==='acid-candidate' &&
                      typeof e.proofPolicy==='string' && Array.isArray(e.layoutLaws) &&
                      typeof d.riskPolicy==='object' && typeof d.renderer==='object'};
                })''')
                contract_ok = {x['id']: x['ok'] for x in contract_audit}
                # Actual public-registry operations, executed independently
                # for EACH registered definition. No DOM catalog card is a
                # substitute for an element-specific runtime/interaction test.
                probe_path = repo / 'tools/mcel_element_contract_probe.js'
                if not probe_path.is_file():
                    checks.append(result('per-element-semantic-contracts', 'UNVERIFIED',
                        details='Missing public API probe source',
                        evidence={'path':str(probe_path)}))
                    contract_results = {}
                else:
                    page.add_script_tag(content=probe_path.read_text(encoding='utf-8'))
                    contract_batch = page.evaluate('() => window.McelAcidElementContracts.run()')
                    contract_results = {r['id']:r for r in contract_batch['elements']}
                    roster = {d['id'] for d in evidence['defs']}
                    bad = sorted(id for id in roster
                                 if id not in contract_results or contract_results[id]['status']!='PASS')
                    rogue = sorted(set(contract_results)-roster)
                    ok = (contract_batch['sourceRosterStable'] and not bad and not rogue and
                          len(contract_batch['elements'])==len(evidence['defs']) and
                          all(len(r['probes']) >= 8 for r in contract_results.values()))
                    checks.append(result('per-element-semantic-contracts', 'PASS' if ok else 'FAIL',
                        details='Executable per-ID registry semantics; NOT proof of widget functionality',
                        evidence={'registered':len(evidence['defs']),
                                  'exercised':len(contract_results),
                                  'probeAssertions':contract_batch['probeCount'],
                                  'failedOrMissing':bad, 'unregisteredResults':rogue,
                                  'sourceRosterStable':contract_batch['sourceRosterStable']}))
                # Independent public-boundary execution, not registry metadata.
                # A failing negative control is an observed implementation defect
                # at the named API boundary, not an absent implementation guess.
                family_witnesses = {}
                core_toolkit_probe = repo / 'tools/mcel_core_toolkit_runtime_probe.js'
                if core_toolkit_probe.is_file():
                    try:
                        page.add_script_tag(content=core_toolkit_probe.read_text(encoding='utf-8'))
                        family_batch = page.evaluate('() => window.McelAcidCoreToolkit.run()')
                        family_witnesses = {w['id']:w for w in family_batch['elements']}
                        definitions = {d['id'] for d in evidence['defs']}
                        missing = sorted(set(family_batch['roster']) - definitions)
                        duplicate = len(family_witnesses) != len(family_batch['elements'])
                        checks.append(result('core-toolkit-public-boundary-behavior',
                            'FAIL' if missing or duplicate else family_batch['status'],
                            details='Executed actual registry serializer and toolkit resolver; '
                                    'negative controls expose contract-enforcement defects at those boundaries',
                            evidence={'roster':family_batch['roster'],
                                      'missingRegisteredIds':missing,'duplicates':duplicate,
                                      'witnesses':family_witnesses,
                                      'boundaryDefects':family_batch['confirmedBoundaryDefects']}))
                    except Exception as exc:
                        checks.append(result('core-toolkit-public-boundary-behavior','FAIL',
                            details=f'Probe execution failed: {type(exc).__name__}: {exc}'))
                else:
                    checks.append(result('core-toolkit-public-boundary-behavior','UNVERIFIED',
                        details='Core/toolkit public boundary probe absent'))
                # Real MCEL FileBasket model/controller, with no application or
                # filesystem effects. The 6 witnesses are explicitly bounded
                # to the reusable Git Tools file-basket consumer, not full
                # implementation/renderer certification of these element IDs.
                selection_probe = repo / 'tools/mcel_file_basket_runtime_probe.js'
                if selection_probe.is_file():
                    try:
                        page.add_script_tag(content=selection_probe.read_text(encoding='utf-8'))
                        selection_batch = page.evaluate('() => window.McelAcidFileBasket.run()')
                        selection_rows = {w['id']:w for w in selection_batch['elements']}
                        registered = {d['id'] for d in evidence['defs']}
                        invalid_roster = (set(selection_batch['roster']) - registered or
                                          set(selection_rows) != set(selection_batch['roster']) or
                                          len(selection_rows) != len(selection_batch['elements']))
                        checks.append(result('file-basket-model-controller-behavior',
                            'FAIL' if invalid_roster else selection_batch['status'],
                            details='Real in-memory file-basket model/controller; bounded state and safety ' 
                                    'proof only, not DOM/runtime registry certification',
                            evidence={'roster':selection_batch['roster'],
                                      'invalidRoster':bool(invalid_roster),
                                      'witnesses':selection_rows,
                                      'failed':selection_batch['failed'],
                                      'executionContext':selection_batch['executionContext']}))
                        # Same per-element output path as the existing boundary
                        # witnesses, with duplicate coverage refused explicitly.
                        duplicate = set(selection_rows) & set(family_witnesses)
                        if duplicate:
                            checks.append(result('file-basket-witness-overlap','FAIL',
                                details='Duplicate per-ID bounded runtime witnesses',
                                evidence=sorted(duplicate)))
                        else:
                            family_witnesses.update(selection_rows)
                    except Exception as exc:
                        checks.append(result('file-basket-model-controller-behavior','FAIL',
                            details=f'Probe execution failed: {type(exc).__name__}: {exc}'))
                else:
                    checks.append(result('file-basket-model-controller-behavior','UNVERIFIED',
                        details='File-basket runtime probe source absent'))
                for d in evidence['defs']:
                    identifier = d['id']
                    # Showcase coverage is a declared subset, not all registry
                    # elements. Uncovered elements create obligations, not passes.
                    structural = 'PASS' if identifier in cards and identifier in acid_ids else (
                        'FAIL' if identifier in acid_ids else 'UNVERIFIED')
                    elements.append({
                        'id': identifier, 'family': d.get('family'),
                        'registrySerialization': 'PASS' if contract_ok.get(identifier) else 'FAIL',
                        'showcasePresence': structural,
                        'functionalBehavior': 'UNVERIFIED',
                        'semanticContractExecution': contract_results.get(identifier, {}).get('status', 'UNVERIFIED'),
                        'semanticContractProbes': contract_results.get(identifier, {}).get('probes', []),
                        'missingBehavioralWitnesses': contract_results.get(identifier, {}).get(
                            'missingBehavioralWitnesses', ['element-runtime-behavior']),
                        'implementationStatus': 'UNKNOWN',
                        'reason': 'No independently executed element-scoped behavior witness admitted',
                        'risk': (d.get('riskPolicy') or {}).get('risk', 'none'),
                        'proofPolicy': d.get('proofPolicy'),
                        'implementationAudit': audit.get(identifier, {}),
                        'boundedRuntimeWitness': family_witnesses.get(identifier,{}).get('status','UNVERIFIED'),
                        'boundedRuntimeEvidence': family_witnesses.get(identifier),
                    })
                checks.append(result('registry-contracts',
                    'PASS' if contract_ok and all(contract_ok.values()) else 'FAIL',
                    evidence={'checked':len(contract_ok),
                              'failed':[k for k,v in contract_ok.items() if not v]}))
                checks.append(result('showcase-subset-consistency',
                    'PASS' if assess_showcase(evidence)['ok'] else 'FAIL',
                    evidence={'defined': len(evidence['defs']), 'showcased': len(cards),
                              'uncovered':sorted({d['id'] for d in evidence['defs']} - acid_ids),
                              'duplicates': evidence['duplicateIds'],
                              'meaning':'Registered subset consistency only, NOT completeness or behavioral proof',
                              'showcaseSelfReportedStatusNotProof': evidence['acid'].get('status'),
                              'assessment': assess_showcase(evidence)}))
                page.locator('[data-mcel-lab-mode="registry"]').click()
                checks.append(result('showcase-navigation', 'PASS' if page.evaluate('''() =>
                    document.querySelector('[data-mcel-lab-mode="registry"]').getAttribute('aria-selected') === 'true'
                    && !document.querySelector('[data-mcel-lab-panel="registry"]').hidden''') else 'FAIL'))
                for width, height in [(1280, 900), (390, 844)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    page.wait_for_timeout(100)
                    geo = page.evaluate('''() => ({ viewport:innerWidth,
                        pageWidth:document.documentElement.scrollWidth,
                        elementCount:document.querySelectorAll('[data-mcel-element]').length,
                        activeModes:document.querySelectorAll('[data-mcel-lab-mode][aria-selected="true"]').length})''')
                    checks.append(result(f'elements-geometry-{width}', 'PASS' if
                        geo['elementCount'] >= len(elements) and geo['activeModes'] == 1 and
                        geo['pageWidth'] <= geo['viewport'] + 2 else 'FAIL', evidence=geo))
                    path = snapshots / f'element-showcase-{width}.png'
                    page.screenshot(path=str(path), full_page=False, animations='disabled')
                    visuals.append(_compare_png(path, baseline, approve, Image, ImageChops))
                if full_elements:
                    # Each registered/showcased element receives an independent
                    # PNG. The 50 presently unshowcased definitions remain
                    # NOT_IMPLEMENTED rather than inventing a rendering.
                    page.set_viewport_size({'width':1280,'height':900})
                    for el in elements:
                        if el['showcasePresence']!='PASS':
                            continue
                        locator=page.locator(
                          '[data-mcel-element-demo-role="catalog-card"][data-mcel-element="'+el['id']+'"]')
                        image_path=snapshots/('element-'+el['id'].replace('.','-')+'.png')
                        locator.screenshot(path=str(image_path), animations='disabled',timeout=10000)
                        visuals.append(_compare_png(image_path,baseline,approve,Image,ImageChops))
                page.close()
                # An independently executed shared runtime witness must never
                # become a pass merely because its registry metadata exists.
                # The compiled-IR fixture keeps these general tests runnable
                # when the optional, frozen Task Manager notebook is absent.
                tab_html = html
                specimen_type = 'canonical-notebook-dsl'
                if tab_html is None:
                    specimen = repo / TAB_SPECIMEN
                    if specimen.is_file():
                        try:
                            tab_ir = json.loads(specimen.read_text(encoding='utf-8'))
                            tab_html = render_notebook_html(tab_ir, fixture={
                                'server_processes':[{'id':'sample','status':'read-only'}],
                                'all_processes':[], 'connections':[], 'hardware':{}})
                            specimen_type = 'checked-in-validated-ir'
                        except (ValueError, OSError, KeyError) as exc:
                            checks.append(result('tab-witness-specimen','FAIL',
                                details=f'Present checked-in IR fixture invalid: {exc}'))
                    else:
                        checks.append(result('tab-witness-specimen','UNVERIFIED',
                            details='No canonical DSL notebook or generic compiled IR fixture'))
                if tab_html is not None:
                    try:
                        runtime_witness = run_tab_witness(browser, tab_html, evidence['defs'])
                        for item in elements:
                            scoped = runtime_witness.get('witnesses', {}).get(item['id'])
                            if scoped:
                                item['boundedRuntimeWitness'] = scoped['status']
                                item['boundedRuntimeEvidence'] = scoped
                        checks.append(result('bounded-tab-runtime-behavior',
                            runtime_witness['status'],
                            details='Five distinct element-scoped behaviors through shared MCEL renderer; '
                                    'does not certify registry renderer binding or full contracts',
                            evidence={'specimen':specimen_type,
                                      'witnesses':runtime_witness.get('witnesses', {}),
                                      'reason':runtime_witness.get('reason'),
                                      'provenance':runtime_witness.get('provenance',{})}))
                    except Exception as exc:
                        checks.append(result('bounded-tab-runtime-behavior','FAIL',
                            details=f'Runtime probe raised {type(exc).__name__}: {exc}'))
                if html is not None:
                    # Independently exercise the already-authored generic notebook;
                    # frozen Task Manager remains unchanged.
                    page = browser.new_page(viewport={'width': 1280, 'height': 850},
                                            device_scale_factor=1, reduced_motion='reduce')
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    requests = []
                    page.on('request', lambda req: requests.append(req.url))
                    page.set_content(html, wait_until='load')
                    tabs = page.locator('[role="tab"]')
                    panels = page.locator('[role="tabpanel"]')
                    checks.append(result('tabs-render', 'PASS' if tabs.count() == panels.count() == 4 else 'FAIL',
                                         evidence={'tabs':tabs.count(), 'panels':panels.count()}))
                    page.locator('[data-mcel-tab="connections"]').click()
                    checks.append(result('tabs-click-transitions', 'PASS' if
                        page.locator('[data-mcel-panel="connections"]').is_visible() and
                        page.locator('[role="tabpanel"]:visible').count() == 1 else 'FAIL'))
                    before = page.evaluate('McelNotebookPreview.getState()')
                    refused = page.evaluate('McelNotebookPreview.select("unknown")')
                    checks.append(result('tabs-invalid-transition-refusal', 'PASS' if
                        refused == {'ok':False, 'code':'invalid-selection', 'effects': []} and
                        page.evaluate('McelNotebookPreview.getState()') == before else 'FAIL'))
                    page.locator('[data-mcel-tab="connections"]').focus()
                    page.keyboard.press('ArrowRight')
                    checks.append(result('tabs-keyboard-navigation', 'PASS' if
                        page.locator('[data-mcel-tab="hardware"]').get_attribute('aria-selected') == 'true' else 'FAIL'))
                    for width, height in [(1280, 850), (390, 844)]:
                        page.set_viewport_size({'width':width, 'height':height})
                        page.wait_for_timeout(100)
                        geo = page.evaluate('''() => ({viewport:innerWidth,
                            pageWidth:document.documentElement.scrollWidth,
                            selected:document.querySelectorAll('[role="tab"][aria-selected="true"]').length,
                            visiblePanels:[...document.querySelectorAll('[role="tabpanel"]')]
                                .filter(e=>!e.hidden && getComputedStyle(e).display!=='none').length})''')
                        checks.append(result(f'notebook-geometry-{width}', 'PASS' if
                            geo['pageWidth'] <= width + 2 and geo['selected'] == 1 and
                            geo['visiblePanels'] == 1 else 'FAIL', evidence=geo))
                        path = snapshots / f'notebook-{width}.png'
                        page.screenshot(path=str(path), full_page=False, animations='disabled')
                        visuals.append(_compare_png(path, baseline, approve, Image, ImageChops))
                    checks.append(result('no-backend-network-effects', 'PASS' if not requests else 'FAIL',
                                         evidence={'requests': requests[:8]}))
                    page.close()
            finally:
                browser.close()
    except Exception as exc:
        return {'status':'FAIL', 'error':f'{type(exc).__name__}: {exc}',
                'elements':elements, 'checks':checks, 'visuals':visuals}
    checks.append(result('browser-exceptions', 'FAIL' if errors else 'PASS', evidence=errors[:8]))
    status = ('FAIL' if any(x['status']=='FAIL' for x in checks) else
              'UNVERIFIED' if any(x['status']=='UNVERIFIED' for x in checks) else
              'PASS' if checks else 'UNVERIFIED')
    return {'status':status, 'elements':elements, 'checks':checks, 'visuals':visuals,
            'elementCount':len(elements), 'browserErrors':errors}


def _compare_png(path: Path, baseline: Path | None, approve: bool, Image, ImageChops):
    entry = {'name':path.name, 'path':path.as_posix(), 'sha256':file_hash(path),
             'status':'UNVERIFIED', 'reason':'visual-baseline-unreviewed',
             'reviewRequired':True}
    if baseline is None:
        return entry
    reference = baseline / path.name
    if approve:
        reference.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, reference)
        entry.update(reason='baseline-captured-for-human-review-not-certified',
                     baseline=reference.as_posix())
        return entry
    if not reference.is_file():
        entry.update(reason='approved-baseline-missing', baseline=reference.as_posix())
        return entry
    try:
        with Image.open(path) as actual, Image.open(reference) as expected:
            if actual.size != expected.size:
                entry.update(status='FAIL', reason='screenshot-dimensions-mismatch',
                             actualSize=list(actual.size), baselineSize=list(expected.size))
                return entry
            diff = ImageChops.difference(actual.convert('RGB'), expected.convert('RGB'))
            bbox = diff.getbbox()
            entry.update(status='PASS' if bbox is None else 'FAIL',
                         reason='pixel-exact-match' if bbox is None else 'visual-diff',
                         baseline=reference.as_posix(), changedBounds=list(bbox) if bbox else None)
            return entry
    except (OSError, ValueError) as exc:
        entry.update(status='FAIL', reason=f'baseline-unreadable: {exc}')
        return entry


def assess_showcase(evidence: dict[str, Any]) -> dict[str, Any]:
    """Judge the observed DOM; the showcase's own `status` is not an oracle.

    This is only a presence/geometry check. No element gets behavioral credit.
    An injected ID without a real rendered card must be rejected.
    """
    defs = [x['id'] for x in evidence['defs']]
    ids = list(evidence['acidIds'])
    cards = list(evidence['observed'])
    card_ids = [x['id'] for x in cards]
    failures = []
    if not defs or len(defs) != len(set(defs)):
        failures.append('empty-or-duplicate-registry')
    if len(ids) != len(set(ids)):
        failures.append('duplicate-showcase-declarations')
    if len(card_ids) != len(set(card_ids)):
        failures.append('duplicate-rendered-cards')
    if set(ids) - set(defs):
        failures.append('showcase-references-unregistered-element')
    if set(ids) != set(card_ids):
        failures.append('declared-showcase-card-mismatch')
    # Cards in an inactive tab are legitimately display:none and have zero
    # geometry. Check dimensions only for observed visible cards; a hidden
    # card is a DOM-presence witness, NOT a layout/functionality witness.
    if any(c.get('visible', False) and (
           not isinstance(c.get('width'), (int,float)) or
           not isinstance(c.get('height'), (int,float)) or
           c['width'] <= 0 or c['height'] <= 0) for c in cards):
        failures.append('nonvisible-card-geometry')
    if evidence.get('duplicateIds'):
        failures.append('duplicate-registered-ids')
    return {'ok':not failures, 'failures':failures,
            'registryCount':len(defs), 'showcaseCount':len(ids),
            'observedCardCount':len(cards)}


def inventory_modules(repo: Path) -> list[dict[str, Any]]:
    """Inventory active MCEL-named sources, excluding patch-run time capsules.

    `tools/patching/reports/new_patch_runs` contains copies of source files in
    extracted bundles and undo archives; scanning those as active source caused
    the reported source count to grow after every patch invocation.

    Inventory is intentionally name-based, not a complete dependency graph or
    a functional certification. Do not follow symlinks outside the repository.
    """
    roots = [
        (repo/'main_computer', ('mcel_*.py',)),
        (repo/'main_computer/web/applications/scripts', ('*mcel*.js',)),
        (repo/'main_computer/web/applications/styles', ('*mcel*.css',)),
        (repo/'main_computer/web/applications/apps', ('*mcel*.html',)),
        (repo/'tools', ('mcel_*.py', 'mcel_*.js')),
    ]
    paths: set[Path] = set()
    ignored_dirs = {'.git', '.venv', '__pycache__', 'node_modules', 'new_patch_runs'}
    for root, patterns in roots:
        if not root.is_dir():
            continue
        for parent, dirs, files in os.walk(root, followlinks=False):
            # Prune historical runs before looking at files, not after counting.
            dirs[:] = sorted(d for d in dirs if d not in ignored_dirs and
                             not (Path(parent)/d).is_symlink())
            parent_path = Path(parent)
            relative = parent_path.relative_to(repo).parts
            if relative[:3] == ('tools', 'patching', 'reports'):
                dirs[:] = [d for d in dirs if d != 'new_patch_runs']
            for filename in files:
                path = parent_path/filename
                if path.is_symlink() or not path.is_file():
                    continue
                if any(path.match(pattern) for pattern in patterns):
                    paths.add(path)
    test_sources = sorted((repo/'tests').glob('test_mcel_*.py'))
    records = []
    for path in sorted(paths, key=lambda p:p.as_posix()):
        stem = path.stem.replace('_','-')
        hits = [p.relative_to(repo).as_posix() for p in test_sources
                if stem in p.stem.replace('_','-')]
        records.append({'path':path.relative_to(repo).as_posix(),
                        'sha256':file_hash(path),
                        'relatedTests':hits,
                        'kind':'source-inventory',
                        'implementationStatus':'UNKNOWN',
                        'reason':'Active name-based inventory; not an executable behavioral witness'})
    return records


def _prepare_output(output: Path) -> Path | None:
    """Recover the v1 runner's ``report.json/`` directory without deleting evidence.

    The original default ``--output`` was a *filename*, but the runner treated it
    as a directory, creating ``.../report.json/`` containing the former reports
    and screenshots. The corrected runner writes ``.../report.json`` as a file.
    Rename the old directory to a free sibling name *before* any expensive tests
    execute. Never remove it, overwrite an existing archive, or follow links.
    """
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'report.json'
    if target.is_symlink():
        raise OSError(f'Report path is a symbolic link; refusing to replace it: {target}')
    if not target.is_dir():
        return None
    destination = output / 'report-json-legacy'
    suffix = 2
    while destination.exists() or destination.is_symlink():
        destination = output / f'report-json-legacy-{suffix}'
        suffix += 1
    try:
        target.rename(destination)
    except OSError as exc:
        raise OSError(
            f'Cannot preserve legacy report directory {target} as {destination}: {exc}'
        ) from exc
    return destination


def run(repo: Path, *, output: Path, mode: str='core', baseline: Path | None=None,
        approve: bool=False, chromium: str | None=None, timeout: int=180) -> dict[str, Any]:
    if mode not in ('core','full','inventory'):
        raise ValueError('Invalid mode')
    legacy_archive = _prepare_output(output)
    before = {p['path']:p['sha256'] for p in inventory_modules(repo)}
    report = {'schema':SCHEMA, 'generatedAt':datetime.now(timezone.utc).isoformat(),
              'status':'INCOMPLETE', 'mode':mode,
              'scope':'name-based MCEL source inventory; dynamically discovered element registry in browser modes',
              'taskManagerFrozen':True, 'modules':[], 'elements':[],
              'suites':[], 'checks':[], 'visuals':[], 'obligations':[],
              'externalAuthorities':[
                  {'name':'MCEL promises', 'path':'tools/promise_coverage/mcel_promises.py',
                   'includedInAcidCounts':False},
                  {'name':'Canonical MCEL truth audit', 'path':'main_computer/mcel_truth_audit.py',
                   'includedInAcidCounts':False}],
              'legacyOutputPreservedAt': str(legacy_archive) if legacy_archive else None}
    if mode != 'inventory':
        suites = CORE_SUITES if mode == 'core' else {
            'all-mcel-python-regressions':tuple(p.relative_to(repo).as_posix()
                for p in sorted((repo/'tests').glob('test_mcel_*.py')))}
        for suite, selectors in suites.items():
            missing_notebook = [p.as_posix() for p in (NOTEBOOK, FIXTURE)
                                if not (repo / p).is_file()]
            if suite == 'projection-and-interaction' and missing_notebook:
                outcome = result(suite, 'UNVERIFIED',
                    details='Projection integration specimen absent from this snapshot',
                    evidence={'missingPaths': missing_notebook, 'selectors':list(selectors)})
            elif suite == 'dsl-and-ir' and missing_notebook:
                # Core compiler and IR guarantees are independent of the
                # optional frozen notebook. Preserve both real test execution
                # and a visible unproven transition/composition obligation.
                independent = [x for x in selectors
                               if x != 'tests/test_mcel_view_transition_composition.py']
                outcome = _run_pytest(repo, independent, timeout)
                report['suites'].append(result('view-transition-composition-specimen',
                    'UNVERIFIED',
                    details='Notebook-dependent integration test cannot run without its specimen',
                    evidence={'missingPaths':missing_notebook,
                              'selectors':['tests/test_mcel_view_transition_composition.py']}))
            else:
                outcome = _run_pytest(repo,list(selectors),timeout)
            outcome['id'] = suite
            report['suites'].append(outcome)
        browser = _run_browser(repo,output,baseline,approve=approve,chromium=chromium,
                               full_elements=(mode=='full'))
        report['elements'] = browser['elements']
        report['checks'] = browser['checks']
        report['visuals'] = browser['visuals']
        if browser.get('error'):
            report['checks'].append(result('chromium-availability',browser['status'],
                details=browser['error']))
    else:
        report['obligations'].append(result('browser-functional-proof','UNVERIFIED',
            details='Inventory mode intentionally does not execute browser tests'))
    report['modules'] = inventory_modules(repo)
    after = {p['path']:p['sha256'] for p in report['modules']}
    report['checks'].append(result('source-immutable','PASS' if before==after else 'FAIL',
        evidence={'added':sorted(set(after)-set(before)),
                  'removed':sorted(set(before)-set(after)),
                  'modified':sorted(x for x in before.keys() & after.keys() if before[x]!=after[x])}))
    # One global quality judgment, distinct from image regression. The old
    # redundant all-elements/all-modules promises are NOT counted as tests.
    report['obligations'].append(result('visual-quality-judgement','UNVERIFIED',
        details='Chromium screenshots and matching pixels cannot certify attractive or accessible design'))

    # PASS/FAIL counts refer only to independently EXECUTED suites, checks and
    # image comparisons. No registry member or source file is secretly treated
    # as an independently executed functional test.
    observed = [x['status'] for key in ('suites','checks','visuals') for x in report[key]]
    report['counts'] = {status:observed.count(status) for status in STATUS}
    element_total = len(report['elements'])
    behavior_proven = sum(x['functionalBehavior']=='PASS' for x in report['elements'])
    registry_serialized = sum(x['registrySerialization']=='PASS' for x in report['elements'])
    showcased = sum(x['showcasePresence']=='PASS' for x in report['elements'])
    unreviewed_pngs = sum(x['status']=='UNVERIFIED' for x in report['visuals'])
    report['coverage'] = {
        'elementRegistryLoaded': mode != 'inventory' and element_total > 0,
        'elements': {
            'registered':element_total if mode != 'inventory' else None,
            'registrySerializationObserved':registry_serialized,
            'semanticContractExecutions':sum(x.get('semanticContractExecution')=='PASS'
                                             for x in report['elements']),
            'semanticContractFailures':sum(x.get('semanticContractExecution')=='FAIL'
                                           for x in report['elements']),
            'semanticContractUnverified':sum(x.get('semanticContractExecution','UNVERIFIED')=='UNVERIFIED'
                                             for x in report['elements']),
            'showcasePresenceObserved':showcased,
            'showcaseNotObserved':element_total-showcased,
            'independentBehaviorProven':behavior_proven,
            'behaviorUnverified':sum(x['functionalBehavior']=='UNVERIFIED' for x in report['elements']),
            'behaviorFailed':sum(x['functionalBehavior']=='FAIL' for x in report['elements']),
            'implementationMissingConfirmed':sum(x['functionalBehavior']=='NOT_IMPLEMENTED'
                                                 for x in report['elements']),
            'definitionSourcesTraced':sum(x.get('implementationAudit',{}).get('definitionLocated') is True
                                           for x in report['elements']),
            'existingTestCandidatesFound':sum(bool(x.get('implementationAudit',{}).get('existingTestCandidates'))
                                              for x in report['elements']),
            'targetedBehaviorTestCandidates':sum(bool(x.get('implementationAudit',{}).get('targetedBehaviorTestCandidates'))
                                                 for x in report['elements']),
            'boundedRuntimeWitnessPassed':sum(x.get('boundedRuntimeWitness')=='PASS'
                                              for x in report['elements']),
            'boundedRuntimeWitnessFailed':sum(x.get('boundedRuntimeWitness')=='FAIL'
                                              for x in report['elements'])},
        'sourceInventory':{'filesDiscovered':len(report['modules']),
                           'excludedHistoricalPaths':['tools/patching/reports/new_patch_runs/'],
                           'behaviorClaimsFromFilenames':0,
                           'completeRepositoryDiscoveryClaim':False},
        'visual':{'screenshotsCompared':len(report['visuals']),
                  'pixelMatches':sum(v['status']=='PASS' for v in report['visuals']),
                  'unreviewed':unreviewed_pngs,
                  'visualQualityProven':False},
        'independentFunctionalCompleteness':False,
    }
    # A single observed failure overrides all coverage gaps. Do NOT allow a
    # self-reported showcase pass or structural registry pass to certify the
    # 141 distinct element behaviors. A module is never an automatic FAIL.
    observed_failure = (any(x=='FAIL' for x in observed) or
                        any(e['registrySerialization']=='FAIL' or e['showcasePresence']=='FAIL' or
                            e['functionalBehavior']=='FAIL' or e.get('semanticContractExecution')=='FAIL' or
                            e.get('boundedRuntimeWitness')=='FAIL'
                            for e in report['elements']))
    unverified = (any(x in ('UNVERIFIED','NOT_IMPLEMENTED') for x in observed) or
                  any(x['status']!='PASS' for x in report['obligations']) or
                  mode=='inventory' or not element_total or behavior_proven != element_total or
                  showcased != element_total)
    report['status'] = 'FAIL' if observed_failure else 'INCOMPLETE' if unverified else 'PASS'
    report['countingPolicy'] = ('counts = executed suite/check/PNG observations only; '
        'coverage = separate elements and source-file inventory; '
        'UNVERIFIED != NOT_IMPLEMENTED; no implied per-element functional proof')
    target = output/'report.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n', encoding='utf-8')
    ec = report['coverage']['elements']
    vc = report['coverage']['visual']
    lines = ['# MCEL functional acid test — evidence report','',
             f"Overall: **{report['status']}**",'',
             '## Independent observed tests (not individual component certifications)',
             f"PASS={report['counts']['PASS']}  FAIL={report['counts']['FAIL']}  "
             f"UNVERIFIED={report['counts']['UNVERIFIED']}  NOT_IMPLEMENTED={report['counts']['NOT_IMPLEMENTED']}",
             '', '## Separate coverage denominators',
             f"- Registered elements: {ec['registered'] if ec['registered'] is not None else 'NOT MEASURED'}",
             f"- Registry metadata serialization checked: {ec['registrySerializationObserved']}",
             f"- Per-element executable semantic contracts passed: {ec['semanticContractExecutions']}",
             f"- Per-element executable semantic contracts failed: {ec['semanticContractFailures']}",
             f"- Showcase DOM presence witnessed: {ec['showcasePresenceObserved']}",
             f"- Missing showcase witnesses: {ec['showcaseNotObserved']}",
             f"- Independently proven element behaviors: {ec['independentBehaviorProven']}",
             f"- Unverified element behaviors: {ec['behaviorUnverified']}",
             f"- Definition locations traced: {ec['definitionSourcesTraced']}",
             f"- Elements with exact literal test references (NOT behavioral proof): {ec['existingTestCandidatesFound']}",
             f"- Elements with targeted existing runtime test candidates: {ec['targetedBehaviorTestCandidates']}",
             f"- Independently executed bounded runtime witnesses: {ec['boundedRuntimeWitnessPassed']} PASS / {ec['boundedRuntimeWitnessFailed']} FAIL",
             f"- Source files inventoried (not functional units): {len(report['modules'])}",
             f"- PNGs compared: {vc['screenshotsCompared']}; unreviewed: {vc['unreviewed']}",
             '- Visual-quality evaluation: UNVERIFIED',
             '', '## Executed suites and browser checks']
    lines += [f"- {x['status']} — {x['id']}: {x.get('details','')}" for key in ('suites','checks')
              for x in report[key]]
    lines += ['', '## PNG regression evidence (not aesthetic approval)']
    lines += [f"- {x['status']} — {x.get('name','unknown')} ({x.get('reason','unspecified')})" for x in report['visuals']]
    lines += ['', '## Per-element evidence gaps']
    lines += [f"- {x['id']} — registry metadata={x['registrySerialization']}, "
              f"semantic API={x.get('semanticContractExecution', 'UNVERIFIED')}, "
              f"showcase presence={x['showcasePresence']}, behavior={x['functionalBehavior']}, "
              f"implementation={x['implementationStatus']}, "
              f"bounded runtime witness={x.get('boundedRuntimeWitness', 'UNVERIFIED')}, "
              f"test candidates={len(x.get('implementationAudit',{}).get('existingTestCandidates', []))}"
              for x in report['elements']]
    lines += ['', '## Evidence scope',
        'The source inventory excludes historical new_patch_runs bundles/undo copies.',
        'The source inventory is a name-based discovery index, not a set of 1:1 behavioral promises.',
        'Per-element API contracts exercise all registry IDs, not component renderer or interactions.',
        'Implementation/test candidates are static navigation clues, not executable proof.',
        'Bounded tab witnesses exercise the actual shared renderer in Chromium; they do not prove entire registered element contracts.',
        'Core/toolkit boundary witnesses exercise public APIs and report invalid child and primitive-gating counterexamples as defects, not widget certifications.',
        'The showcase self-reported pass is nonauthoritative; actual DOM presence is checked.',
        'External release gates and architectural promises are not blended into these counts.',
        'Missing witness = UNVERIFIED; absent implementation is NOT_IMPLEMENTED only after independent proof.']
    (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report


def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=REPORT_PATH)
    parser.add_argument('--mode',choices=('core','full','inventory'),default='core')
    parser.add_argument('--baseline-dir',type=Path)
    parser.add_argument('--approve-baselines',action='store_true',
                        help='Capture candidate PNGs for human review; DOES NOT certify visual quality')
    parser.add_argument('--chromium-executable')
    parser.add_argument('--timeout-seconds',type=int,default=180)
    args=parser.parse_args(argv)
    if args.approve_baselines and not args.baseline_dir:
        parser.error('--approve-baselines requires --baseline-dir')
    try:
        report = run(args.repo_root.resolve(),output=(args.repo_root/args.output).resolve() if
                     not args.output.is_absolute() else args.output,
                     mode=args.mode,baseline=args.baseline_dir,
                     approve=args.approve_baselines,chromium=args.chromium_executable,
                     timeout=args.timeout_seconds)
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    print(f"MCEL_FUNCTIONAL_ACID_{report['status']}: {report['counts']}")
    print(f"elements={len(report['elements'])} modules={len(report['modules'])}")
    if report.get('legacyOutputPreservedAt'):
        print('preserved legacy report directory:', report['legacyOutputPreservedAt'])
    print('report:',args.output)
    # An incomplete suite is intentionally not advertised as globally passed;
    # exit 2 is incomplete, exit 1 is a real failure.
    return 1 if report['status']=='FAIL' else 2 if report['status']=='INCOMPLETE' else 0


if __name__=='__main__':
    raise SystemExit(main())
