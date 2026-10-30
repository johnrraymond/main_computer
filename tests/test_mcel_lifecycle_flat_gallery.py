"""The operator-facing lifecycle gallery is intentionally reduced to L18-01 only.

Keep the 18-run orthogonal screening data in the runtime; no longer mount 18
independent live examples while the first example's hover contract is debugged.
"""
from __future__ import annotations
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
JS=ROOT/'main_computer/web/applications/scripts/mcel-lifecycle-acid-lab.js'
HTML=ROOT/'main_computer/web/applications/apps/mcel-lab.html'


def test_only_first_case_rendered_but_orthogonal_matrix_retained():
    html=HTML.read_text('utf-8')
    js=JS.read_text('utf-8')
    assert html.index('id="mcel-lifecycle-acid-gallery"') < html.index('id="mcel-lifecycle-acid-workbench"')
    assert 'const LIVE_EXPERIMENTS=Object.freeze([EXPERIMENTS[0]])' in js
    assert 'for(const exp of LIVE_EXPERIMENTS)galleryCard(exp)' in js
    assert 'L18-01 · One live experiment.' in html
    assert 'function validateArray()' in js


@pytest.mark.parametrize('width', [390,1440])
def test_first_only_gallery_has_twenty_rows_and_working_controls(width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_lifecycle_taguchi_acid_frob import install
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            page=browser.new_page(viewport={'width':width,'height':950}, reduced_motion='reduce',accept_downloads=True)
            errors=[]
            page.on('pageerror',lambda e: errors.append(str(e)))
            install(page)
            cards=page.locator('#mcel-lifecycle-acid-gallery > [data-case-id]')
            assert cards.count()==1
            first=cards.first
            assert first.get_attribute('data-case-id')=='L18-01'
            assert first.locator('mcel-row').count()==20
            assert first.locator('mcel-row').evaluate_all('(rows)=>new Set(rows.map(r=>r.dataset.rowId)).size')==20
            assert page.locator('#mcel-lifecycle-acid-case-picker option').count()==1
            assert page.locator('#mcel-lifecycle-acid-matrix tr[data-run]').count()==1
            assert page.locator('[data-case-id="L18-02"]').count()==0
            assert first.locator('select[aria-label="L18-01 overscroll handoff threshold"]').input_value()=='15'
            first.locator('select[aria-label="L18-01 overscroll handoff threshold"]').select_option('20')
            assert first.locator('mcel-row').count()==20
            first.locator('[data-verdict="broken"]').click()
            first.locator('textarea').fill('Only first example is available for inspection.')
            with page.expect_download() as download:
                page.locator('#mcel-lifecycle-acid-gallery-export').click()
            result=json.loads(Path(download.value.path()).read_text('utf-8'))
            assert [c['id'] for c in result['verticalGallery']['cases']]==['L18-01']
            assert [r['id'] for r in result['manualReviews']]==['L18-01']
            assert result['verticalGallery']['cases'][0]['handoffThresholdPercent']==20
            assert not errors,errors
            page.close()
        finally:
            browser.close()
