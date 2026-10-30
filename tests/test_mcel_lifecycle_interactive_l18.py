"""The advanced live case editor must not expose deleted L18 examples."""
from pathlib import Path
import pytest
from tools.mcel_lifecycle_taguchi_acid_frob import install
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

@pytest.mark.parametrize('width', [390, 1440])
def test_only_first_advanced_case_is_available(width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page(viewport={'width':width,'height':950},accept_downloads=True)
        errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        try:
            install(page)
            assert page.locator('#mcel-lifecycle-acid-case-picker option').count()==1
            assert page.locator('#mcel-lifecycle-acid-case-picker').input_value()=='L18-01'
            assert page.locator('#mcel-lifecycle-acid-matrix tr[data-run]').count()==1
            assert page.locator('#mcel-lifecycle-acid-prev').is_disabled()
            assert page.locator('#mcel-lifecycle-acid-next').is_disabled()
            advanced=page.locator('.mcel-lifecycle-acid__single-advanced')
            advanced.locator('summary').click()
            assert page.locator('#mcel-lifecycle-acid-preview mcel-row').count()>=2
            page.locator('[data-mcel-acid-verdict="broken"]').click()
            page.locator('#mcel-lifecycle-acid-notes').fill('Investigating first example only.')
            assert '1 of 1 reviewed' in page.locator('#mcel-lifecycle-acid-review-progress').inner_text()
            assert 'L18-01' in page.locator('#mcel-lifecycle-acid-case-info').inner_text()
            assert not errors,errors
        finally:
            page.close()
            browser.close()
