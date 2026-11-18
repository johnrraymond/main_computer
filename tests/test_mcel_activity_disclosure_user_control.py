"""Operation evidence must not operate its own disclosure caret.

Repeated failures may update receipts and the latest-request summary, but the
user is the only authority that opens or closes the Activity disclosure.
"""

from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_generated_host import render_generated_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_activity_disclosure_preserves_user_choice_across_failed_reads():
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        page = browser.new_page()
        try:
            page.evaluate("""() => {
              window.fetch = async () => ({ok:false,status:503,json:async()=>({error:'unavailable'})});
            }""")
            page.set_content(render_generated_page())
            details = page.locator('details[data-mcel-activity]')
            assert details.count() == 1
            assert details.evaluate('node => node.open') is False
            refresh = page.locator('form[data-intent="intent:refresh"] button[type="submit"]')
            refresh.click()
            page.wait_for_function('''() => document.querySelectorAll('[data-mcel-activity] li').length === 1''')
            assert 'failed' in details.locator('li').first.text_content()
            assert details.evaluate('node => node.open') is False

            details.locator('summary').click()
            assert details.evaluate('node => node.open') is True
            refresh.click()
            page.wait_for_function('''() => document.querySelectorAll('[data-mcel-activity] li').length === 2''')
            assert details.evaluate('node => node.open') is True

            details.locator('summary').click()
            assert details.evaluate('node => node.open') is False
            refresh.click()
            page.wait_for_function('''() => document.querySelectorAll('[data-mcel-activity] li').length === 3''')
            assert details.evaluate('node => node.open') is False
        finally:
            page.close()
            browser.close()
