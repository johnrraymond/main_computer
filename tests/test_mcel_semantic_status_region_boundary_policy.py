"""Purpose determines region placement; surface geometry determines safe content bounds.

The shared workbench stylesheet must not erase status-region panel geometry,
while genuinely unboxed status text stays unboxed. No application-specific CSS.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page


STYLE = Path(__file__).resolve().parents[1] / 'main_computer/web/applications/styles/mcel-semantic-workbench.css'


def test_status_purpose_rule_does_not_claim_box_model_authority():
    import re
    css = STYLE.read_text(encoding='utf8')
    rules = re.findall(r'[^{}]*\[data-mcel-purpose="status"\]\s*\{([^}]*)\}', css)
    assert rules, 'Shared status placement rule must remain explicit'
    for declarations in rules:
        # Independent region geometry must never be nullified by purpose alone.
        assert not re.search(r'(?:^|;)\s*(?:padding(?:-[a-z]+)?|border(?:-[a-z]+)?|'
                             r'background(?:-[a-z]+)?|border-radius|box-shadow)\s*:', declarations), declarations


@pytest.mark.parametrize('app', ['file-manager', 'task-manager', 'code-editor'])
@pytest.mark.parametrize('width', [390, 1280])
def test_status_region_geometry_comes_from_surface_not_purpose(app, width):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': width, 'height': 850})
            # Read-only offline mode; no live host side effects.
            page.evaluate('''() => {window.fetch=async()=>({ok:false,status:404,
              json:async()=>({error:'offline'})});}''')
            page.set_content(render_semantic_page(app))
            page.wait_for_selector('[data-mcel-region][data-mcel-purpose="status"]')
            actual = page.evaluate('''() => [...document.querySelectorAll(
              '[data-mcel-presentation="expert"] [data-mcel-region][data-mcel-purpose="status"]')]
              .filter(el=>el.getClientRects().length).map(el=>{
                const style=getComputedStyle(el),rect=el.getBoundingClientRect(),
                  heading=el.querySelector('h2'), headingRect=heading?.getBoundingClientRect();
                return {padding:parseFloat(style.paddingLeft),radius:parseFloat(style.borderTopLeftRadius),
                  border:parseFloat(style.borderLeftWidth),background:style.backgroundColor,
                  headingInset:headingRect?.left-rect.left, width:rect.width};
              })''')
            assert actual, (app, width)
            for region in actual:
                assert region['width'] > 0
                if region['radius'] > 0:
                    assert region['padding'] >= region['radius'] - region['border'] - 2, (app, width, region)
                    assert region['headingInset'] >= region['padding'], (app, width, region)
                else:
                    # A true strip is not forced into a box by status semantics.
                    assert region['padding'] == 0 and region['border'] == 0, (app, width, region)
            if app == 'code-editor':
                assert all(r['radius'] == 0 and r['padding'] == 0 for r in actual), (width, actual)
            else:
                assert all(r['radius'] == 12 and r['padding'] >= 10 for r in actual), (app, width, actual)
        finally:
            browser.close()
