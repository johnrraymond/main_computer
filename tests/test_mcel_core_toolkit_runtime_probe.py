"""Executable core/toolkit public API witnesses and controls in real Chromium.

Known failures are assertions *about the defects*, not expected MCEL success.
The cumulative acid test records the actual failing behavior as FAIL.
"""
from __future__ import annotations
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main_computer/web/applications/scripts'
PROBE = ROOT / 'tools/mcel_core_toolkit_runtime_probe.js'
SCRIPTS = ('mcel-element-registry.js', 'mcel-elements-core.js', 'mcel-toolkit-core.js')


@pytest.fixture
def live_page():
    playwright = pytest.importorskip('playwright.sync_api')
    import shutil
    with playwright.sync_playwright() as pw:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        args = {'headless': True}
        if executable:
            args.update(executable_path=executable, args=['--no-sandbox'])
        try:
            browser = pw.chromium.launch(**args)
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page()
            page.set_content('<!doctype html><html><body></body></html>')
            for name in SCRIPTS:
                page.add_script_tag(content=(SOURCE/name).read_text(encoding='utf-8'))
            page.add_script_tag(content=PROBE.read_text(encoding='utf-8'))
            yield page
        finally:
            browser.close()


def test_probe_roster_tracks_actual_registry(live_page):
    result = live_page.evaluate('McelAcidCoreToolkit.run()')
    reg = live_page.evaluate('McelElementRegistry.all().map(x=>x.id)')
    assert set(result['roster']).issubset(set(reg))
    assert len(result['elements']) == len(result['roster']) == 10
    assert set(r['id'] for r in result['elements']) == set(result['roster'])
    assert all(r['fullElementCertification'] is False for r in result['elements'])
    assert all(r['scope'] for r in result['elements'])


def test_observation_envelope_is_complete_without_hiding_failures(live_page):
    result = live_page.evaluate('McelAcidCoreToolkit.run()')
    assert len(result['elements']) == 10
    assert result['status'] == ('FAIL' if any(r['failedObservations'] for r in result['elements']) else 'PASS')
    assert set(x['id'] for x in result['confirmedBoundaryDefects']) == {
        r['id'] for r in result['elements'] if r['status']=='FAIL'}


def test_core_allowed_children_are_enforced_at_public_serialization_boundary(live_page):
    result = live_page.evaluate('McelAcidCoreToolkit.run()')
    rows = {r['id']:r for r in result['elements']}
    for id_ in live_page.evaluate('McelAcidCoreToolkit.CORE_PARENTS'):
        assert rows[id_]['observations']['legalChildRetained'],id_
        assert rows[id_]['observations']['forbiddenChildRefused'],(id_,rows[id_]['details'])


def test_toolkit_view_resolver_enforces_required_primitives(live_page):
    result=live_page.evaluate('McelAcidCoreToolkit.run()')
    row=next(r for r in result['elements'] if r['id']=='element.toolkit.view-resolver')
    assert row['observations']['missingPrimitiveRefused'],row['details']
    assert row['observations']['rejectionReasonConsistent'],row['details']


def test_prior_contract_defects_are_caught_by_the_same_unchanged_probe(live_page):
    """Fault injection must cause the seven original witnesses to fail.

    The real implementation now passes. Deliberately restore both old bugs
    only inside this disposable page and demand that v5 detects them.
    """
    assert live_page.evaluate('McelAcidCoreToolkit.run().status') == 'PASS'
    live_page.evaluate('''() => {
      const reg = McelElementRegistry;
      const original = reg.serializeElement;
      reg.serializeElement = function (id, props={}) {
        const result = original(id, {...props, children: []});
        result.children = props.children || []; // buggy legacy serializer
        return result;
      };
      const tk = McelToolkitCore;
      const evaluate = tk.evaluateView;
      tk.evaluateView = function (contract, view) {
        const result = evaluate(contract, view);
        if (result.missingCapabilities.length === 0 &&
            !(contract.mustReject || []).includes(view.id) &&
            result.missingPrimitives.length > 0) {
          result.eligible = true; // buggy legacy capability-only eligibility
          result.reason = 'Eligible: capabilities sufficient';
        }
        return result;
      };
    }''')
    broken = live_page.evaluate('McelAcidCoreToolkit.run()')
    assert broken['status'] == 'FAIL'
    assert {entry['id'] for entry in broken['confirmedBoundaryDefects']} == (
        set(live_page.evaluate('McelAcidCoreToolkit.CORE_PARENTS')) |
        {'element.toolkit.view-resolver'}
    )


def test_negative_control_broken_primitive_catalog_is_detected(live_page):
    live_page.evaluate('''() => { McelToolkitCore.getPrimitive=()=>null; }''')
    result=live_page.evaluate('McelAcidCoreToolkit.run()')
    row=next(x for x in result['elements'] if x['id']=='element.toolkit.foundation-token')
    assert row['status']=='FAIL' and 'primitiveLinked' in row['failedObservations']


def test_audit_traces_witness_to_actual_implementation():
    from tools.mcel_element_behavior_audit import audit, CORE_TOOLKIT
    import re
    source=(SOURCE/'mcel-elements-core.js').read_text(encoding='utf-8')
    definitions=[{'id':id_} for id_ in re.findall(r'\bdef\(\s*["\'](element\.[\w.-]+)["\']',source)]
    report=audit(ROOT,definitions)
    for id_, (scope, implementation) in CORE_TOOLKIT.items():
        row=report[id_]
        assert row['candidateWitness']==scope
        assert implementation in row['runtimeImplementationCandidates']
        assert 'tests/test_mcel_core_toolkit_runtime_probe.py' in row['targetedBehaviorTestCandidates']
        assert row['fullBehaviorProvenByStaticAudit'] is False
