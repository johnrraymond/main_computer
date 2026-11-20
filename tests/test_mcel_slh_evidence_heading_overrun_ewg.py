"""An existing E/W/G boundary invariant must be obeyed by shared layout CSS.

No production diagnostic exemptions: a deliberate corner collision still yields
an active error, and removing that violation resolves it in incident history.
"""
from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_slh_startup_ewg import MOCK


@pytest.mark.parametrize('width', [390, 680, 1000, 1320])
def test_operation_evidence_heading_respects_rounded_panel_and_ewg(width):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 800})
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_selector('[data-mcel-diagnostic-title-control]')
            baseline = page.evaluate('''() => {
              const panel=document.querySelector('[data-mcel-region="interface-region:generated-evidence"]');
              const heading=panel.querySelector('h2');
              const r=panel.getBoundingClientRect(),h=heading.getBoundingClientRect();
              const s=getComputedStyle(panel);
              const snapshot=MCELDiagnosticEvidence.snapshot('slh');
              return {inset:h.left-r.left,padding:parseFloat(s.paddingLeft),
                radius:parseFloat(s.borderTopLeftRadius),border:parseFloat(s.borderLeftWidth),
                collisions:snapshot.findings.filter(f=>f.code==='heading-text-region-overrun')};
            }''')
            assert baseline['radius'] == 12
            assert baseline['padding'] >= 10
            assert baseline['inset'] >= baseline['padding']
            assert baseline['collisions'] == [], (width, baseline)

            # Deliberately reintroduce exactly the old CSS defect at runtime.
            # The detector must continue to catch it with an ERROR.
            before = page.evaluate('''() => {
              const panel=document.querySelector('[data-mcel-region="interface-region:generated-evidence"]');
              panel.style.setProperty('padding','0','important');
              const current=MCELDiagnosticsCounterWidget.refresh('slh');
              const evidence=MCELDiagnosticEvidence.snapshot('slh');
              return {findings:evidence.findings.filter(f=>f.code==='heading-text-region-overrun'),
                counts:current.counts,
                visible:document.querySelector('[data-mcel-diagnostics-counter="slh"]').dataset.errors};
            }''')
            assert len(before['findings']) == 1, (width, before)
            issue = before['findings'][0]
            assert issue['severity'] == 'error'
            assert issue['identity']['region'] == 'interface-region:generated-evidence'
            assert issue['identity']['tag'] == 'h2'
            assert any(c['kind'] == 'rounded-corner' and c['corner'] == 'top-left'
                       and c['penetrationPx'] > 1 for c in issue['evidence']['collisions'])
            assert before['counts']['errors'] >= 1
            assert int(before['visible']) >= 1

            widget = page.locator('[data-mcel-diagnostics-counter="slh"]')
            widget.click()
            page.wait_for_function('window.copies.length === 1')
            copied = page.evaluate('window.copies[0]')
            assert copied['current']['counts']['errors'] >= 1
            assert any(x['code'] == 'heading-text-region-overrun' and x['severity'] == 'error'
                       for x in copied['diagnosticEvidence']['findings'])
            assert any(x['code'] == 'heading-text-region-overrun'
                       and x['normalizedSeverity'] == 'error' for x in copied['history']['issues'])

            restored = page.evaluate('''() => {
              const panel=document.querySelector('[data-mcel-region="interface-region:generated-evidence"]');
              panel.style.removeProperty('padding');
              const current=MCELDiagnosticsCounterWidget.refresh('slh');
              return {codes:current.report.findings.map(f=>f.code),
                history:MCELDiagnosticsCounterWidget.getStatus('slh').history.entries
                  .filter(x=>x.code==='heading-text-region-overrun')};
            }''')
            assert 'heading-text-region-overrun' not in restored['codes']
            assert len(restored['history']) == 1
            assert restored['history'][0]['currentlyActive'] is False
        finally:
            browser.close()


def test_detector_is_generic_not_tied_to_slh_heading_or_region_name():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': 1000, 'height': 800})
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            data = page.evaluate('''() => {
              const root=document.createElement('main');
              root.innerHTML='<section data-mcel-region="interface-region:another-panel" '
                +'style="background:#24303a;border-radius:12px;width:300px;padding:0">'
                +'<h2 style="margin:0;font-size:14px;line-height:18px">Unrelated title</h2></section>';
              document.body.appendChild(root);
              MCELDiagnosticEvidence.register('unrelated',root,{application:{appId:'unrelated'}},null);
              const unsafe=MCELDiagnosticEvidence.snapshot('unrelated').findings
                .filter(f=>f.code==='heading-text-region-overrun');
              root.firstElementChild.style.padding='16px';
              const safe=MCELDiagnosticEvidence.snapshot('unrelated').findings
                .filter(f=>f.code==='heading-text-region-overrun');
              return {unsafe,safe};
            }''')
            assert len(data['unsafe']) == 1
            assert data['unsafe'][0]['severity'] == 'error'
            assert data['safe'] == []
        finally:
            browser.close()
