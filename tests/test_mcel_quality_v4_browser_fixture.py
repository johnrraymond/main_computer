"""Browser-level proof that synthetic long text reaches visible records and resets."""
from __future__ import annotations
import unittest
from pathlib import Path
from tools import mcel_semantic_generate as generator
from tools.mcel_browser_launch import launch_chromium, BrowserLaunchError


class LongTextFixtureBrowserTests(unittest.TestCase):
    def test_long_text_visible_and_reset(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)
            self.assertEqual(generator.run(generator.DEFAULT_DESIGN,output,verify=False),0)
            preview=(output/'preview.html').read_text(encoding='utf-8')
            try:
                with sync_playwright() as pw:
                    browser,_=launch_chromium(pw,headless=True,args=['--no-sandbox'])
                    try:
                        page=browser.new_page(viewport={'width':1440,'height':900})
                        page.set_content(preview,wait_until='load')
                        page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                        measure="""() => [...document.querySelectorAll('.record')].filter(r=>!r.hidden&&r.getClientRects().length).map(r=>r.innerText)"""
                        baseline=page.evaluate(measure)
                        self.assertTrue(baseline)
                        page.evaluate("()=>window.__MCEL_SET_VISUAL_FIXTURE('long_text')")
                        changed=page.evaluate(measure)
                        self.assertNotEqual(baseline,changed)
                        self.assertTrue(any('Synthetic long command:' in r for r in changed))
                        self.assertTrue(page.locator('[data-stress-fixture="long-text"]').count())
                        page.evaluate("()=>window.__MCEL_SET_VISUAL_FIXTURE('default')")
                        self.assertEqual(page.evaluate(measure),baseline)
                    finally:
                        browser.close()
            except BrowserLaunchError:
                self.skipTest('No available Chromium family browser')

if __name__=='__main__': unittest.main()
