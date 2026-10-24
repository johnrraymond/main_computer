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


def test_corrected_behavior_would_pass_same_probe_without_change_to_probe(live_page):
    # Simulate hypothetical fixes only inside a disposable page. Nothing in
    # production or the harness is altered. This proves the witness detects
    # corrected behavior instead of hard-coding FAIL for the known defects.
    live_page.evaluate('''() => {
      const reg=McelElementRegistry;
      const original=reg.serializeElement;
      reg.serializeElement=function(id,props={}) {
        const d=reg.get(id);
        const limited = {...props, children:(props.children||[]).filter(x=>d?.allowedChildren.includes(x.elementId))};
        return original(id,limited);
      };
      const tk=McelToolkitCore;
      const evaluate=tk.evaluateView;
      tk.evaluateView=function(contract,view) {
        const out=evaluate(contract,view);
        if(out.missingPrimitives.length) {
          out.eligible=false;
          out.reason='Rejected: missing primitives '+out.missingPrimitives.join(',');
        }
        return out;
      };
    }''')
    result=live_page.evaluate('McelAcidCoreToolkit.run()')
    assert result['status']=='PASS',result['confirmedBoundaryDefects']
    assert all(row['status']=='PASS' for row in result['elements'])


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
