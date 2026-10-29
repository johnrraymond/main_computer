"""Patch 14.7.5a follow-up: each L18 run must be manually exercisable, not just benchmarked."""
import json
from pathlib import Path
import pytest
from tools.mcel_lifecycle_taguchi_acid_frob import install
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome


@pytest.mark.parametrize('width', [390, 1440])
def test_all_18_cases_are_immediately_interactive_with_review_and_export(width, tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=_chrome(p,None)
        page=browser.new_page(viewport={'width':width,'height':950},accept_downloads=True)
        errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        try:
            install(page)
            assert page.locator('#mcel-lifecycle-acid-case-picker option').count()==18
            assert page.locator('#mcel-lifecycle-acid-case-picker').input_value()=='L18-01'
            assert page.locator('#mcel-lifecycle-acid-preview mcel-row').count()==2
            # Live case starts without running the destructive source/selection script.
            assert page.locator('#mcel-lifecycle-acid-overview').text_content().find('0/18')>=0
            assert page.locator('#mcel-lifecycle-acid-step-status').inner_text().startswith('Step 1/')
            page.locator('#mcel-lifecycle-acid-step').click()
            assert page.locator('#mcel-lifecycle-acid-step-status').inner_text().startswith('Step 2/')
            page.locator('#mcel-lifecycle-acid-step').click()
            assert 'All planned conditions applied' in page.locator('#mcel-lifecycle-acid-step-status').inner_text()
            page.locator('#mcel-lifecycle-acid-next').click()
            assert page.locator('#mcel-lifecycle-acid-case-picker').input_value()=='L18-02'
            assert page.locator('#mcel-lifecycle-acid-preview mcel-row').count()==2
            page.locator('[data-mcel-acid-verdict="broken"]').click()
            page.locator('#mcel-lifecycle-acid-notes').fill('The floating card obscures the value I am copying.')
            page.locator('#mcel-lifecycle-acid-prev').click()
            page.locator('#mcel-lifecycle-acid-next').click()
            assert page.locator('[data-mcel-acid-verdict="broken"]').get_attribute('aria-pressed')=='true'
            assert 'obscures' in page.locator('#mcel-lifecycle-acid-notes').input_value()
            assert '1 of 18 reviewed' in page.locator('#mcel-lifecycle-acid-review-progress').inner_text()
            # Each table row is a direct live launch, never just loads dropdowns.
            page.locator('#mcel-lifecycle-acid-matrix tr[data-run="L18-18"] button').click()
            assert page.locator('#mcel-lifecycle-acid-case-picker').input_value()=='L18-18'
            assert page.locator('#mcel-lifecycle-acid-preview mcel-row').count()>=2
            page.locator('[data-mcel-acid-action="source update"]').click()
            assert 'source update' in page.locator('#mcel-lifecycle-acid-feedback').inner_text()
            page.locator('.mcel-lifecycle-acid__automated summary').click()
            with page.expect_download() as info:
                page.locator('#mcel-lifecycle-acid-export').click()
            path=Path(info.value.path())
            data=json.loads(path.read_text())
            assert len(data['manualReviews'])==18
            review=next(r for r in data['manualReviews'] if r['id']=='L18-02')
            assert review['verdict']=='broken' and 'obscures' in review['notes']
            assert data['humanReviewed']==1
            assert any(r['events'] for r in data['manualReviews'])
            # All 18 independent Taguchi assignments must create a fresh, usable live mount.
            for i in range(1, 19):
                case=f'L18-{i:02d}'
                page.locator('#mcel-lifecycle-acid-case-picker').select_option(case)
                assert page.locator('#mcel-lifecycle-acid-preview mcel-row').count()>=2, case
                assert case in page.locator('#mcel-lifecycle-acid-case-info').inner_text()
                assert page.locator('#mcel-lifecycle-acid-step-status').inner_text().startswith('Step 1/'), case
            assert not errors, errors
        finally:
            page.close()
            browser.close()
