"""The repository-wide acid test may only credit independently observed behavior."""
from pathlib import Path
import json
import shutil

import pytest

from tools.mcel_element_behavior_audit import audit, TABBED, DEFINITION
from tools.mcel_element_runtime_probe import TAB_IDS, assess_witnesses, run_tab_witness
from main_computer.mcel_notebook_projection import render_notebook_html

ROOT = Path(__file__).resolve().parents[1]
SPECIMEN = ROOT / 'tools/fixtures/mcel_acid_tab_ir.json'


def _roster():
    # The actual source declarations, not a fabricated hand-maintained roster.
    import re
    source = (ROOT / DEFINITION).read_text(encoding='utf-8')
    return [{'id':value,'renderer':{'htmlTag':'mcel-'+value.split('.')[-1]},
             'kind':'element', 'stateModel':{}, 'interactionModel':{}}
            for value in re.findall(r'\bdef\(\s*["\'](element\.[\w.-]+)["\']',source)]


def test_all_registered_definitions_have_one_traceable_source():
    rows = audit(ROOT, _roster())
    assert len(rows) == 141
    assert all(v['definitionLocated'] for v in rows.values())
    assert all(len(v['definitionLocation']['lines'])==1 for v in rows.values())
    assert all(v['implementationExistence']=='UNKNOWN' for v in rows.values())
    assert all(not v['fullBehaviorProvenByStaticAudit'] for v in rows.values())


def test_test_references_are_candidates_not_witnesses():
    rows = audit(ROOT, _roster())
    assert any(row['existingTestCandidates'] for row in rows.values())
    assert len(rows['element.toolkit.tab']['runtimeImplementationCandidates'])==2
    assert rows['element.core.app']['runtimeImplementationCandidates']==[
        'main_computer/web/applications/scripts/mcel-element-registry.js']
    assert all(row['auditAssessment']=='definition-and-candidates-only' for row in rows.values())


def test_missing_definition_remains_explicit(tmp_path):
    result = audit(tmp_path,[{'id':'element.core.app','kind':'root'}])
    assert not result['element.core.app']['definitionLocated']
    assert result['element.core.app']['implementationExistence']=='UNKNOWN'
    assert result['element.core.app']['existingTestCandidates']==[]


def test_no_renderer_claim_from_metadata_alone():
    row = audit(ROOT,[{'id':'element.nonexistent.renderer','renderer':{'htmlTag':'custom-widget'},
                      'stateModel':{'required':['x']},'interactionModel':{'click':'toggle'},
                      'riskPolicy':{'risk':'destructive'}}])['element.nonexistent.renderer']
    assert row['runtimeImplementationCandidates']==[]
    assert row['fullBehaviorProvenByStaticAudit'] is False
    assert 'forbidden-consequential-effects' in row['outstandingContractDimensions']
    assert 'interaction-transition-and-boundaries' in row['outstandingContractDimensions']


def test_tab_witness_negative_control_rejects_broken_aria_and_panel():
    values = {k:True for k in ('rendererGenerated','oneSelected','oneVisiblePanel','panelModelPreserved',
        'tablistRole','keyboardMovedFocus','keyboardSelected','clickSelected',
        'tabAriaControlsValid','tabAriaSelectedValid','tabpanelBacklinkValid',
        'inactivePanelHidden','invalidRefused','invalidPreservedState','effectsEmpty',
        'routeStateSynced')}
    good = assess_witnesses(values,set(TAB_IDS))
    assert good['status']=='PASS'
    assert len(good['witnesses'])==5
    for key, expected_id in (('tabAriaControlsValid','element.toolkit.tab'),
                             ('panelModelPreserved','element.toolkit.tab-panel'),
                             ('keyboardMovedFocus','element.toolkit.tab-list'),
                             ('invalidRefused','element.toolkit.tab-controller')):
        broken = dict(values, **{key:False})
        bad=assess_witnesses(broken,set(TAB_IDS))
        assert bad['status']=='FAIL'
        assert bad['witnesses'][expected_id]['status']=='FAIL'
    assert assess_witnesses(values,set(TAB_IDS)-{TAB_IDS[0]})['status']=='UNVERIFIED'
    assert assess_witnesses({},set(TAB_IDS))['status']=='FAIL'


def test_shared_runtime_witness_exercises_real_browser():
    playwright = pytest.importorskip('playwright.sync_api')
    ir = json.loads(SPECIMEN.read_text(encoding='utf-8'))
    html = render_notebook_html(ir,fixture={'server_processes':[{'id':'acid'}]})
    executable = shutil.which('chromium') or shutil.which('chromium-browser')
    with playwright.sync_playwright() as pw:
        kwargs = {'headless':True}
        if executable:
            kwargs.update(executable_path=executable,args=['--no-sandbox'])
        try:
            browser = pw.chromium.launch(**kwargs)
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            out = run_tab_witness(browser,html,[{'id':x} for x in TAB_IDS])
            assert out['status']=='PASS',out
            assert len(out['witnesses'])==5
            assert all(row['status']=='PASS' and row['fullElementCertification'] is False
                       for row in out['witnesses'].values())
            assert out['provenance']['networkRequests']==[]
            assert out['provenance']['browserExceptions']==[]
        finally:
            browser.close()


def test_real_dom_tampering_is_detected_in_disposable_chromium():
    playwright = pytest.importorskip('playwright.sync_api')
    ir = json.loads(SPECIMEN.read_text(encoding='utf-8'))
    html = render_notebook_html(ir, fixture={'server_processes':[{'id':'acid'}]})
    executable = shutil.which('chromium') or shutil.which('chromium-browser')
    with playwright.sync_playwright() as pw:
        kwargs = {'headless':True}
        if executable:
            kwargs.update(executable_path=executable, args=['--no-sandbox'])
        try:
            browser = pw.chromium.launch(**kwargs)
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            def corrupt(panel_page):
                panel_page.locator('[role="tabpanel"]').first.evaluate(
                    '(el) => el.setAttribute("aria-labelledby", "broken-link")')
            out = run_tab_witness(browser,html,[{'id':x} for x in TAB_IDS],
                                  mutate_for_negative_control=corrupt)
            assert out['status']=='FAIL'
            assert out['witnesses']['element.toolkit.tab-panel']['status']=='FAIL'
            assert 'tabpanelBacklinkValid' in out['witnesses']['element.toolkit.tab-panel']['failedObservations']
        finally:
            browser.close()
