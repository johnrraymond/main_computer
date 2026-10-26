"""Browser proofs for generic MCEL content-aware record composition (research only)."""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from tools import mcel_semantic_generate as generator
from tools import mcel_design_quality_frob as quality
from tools.mcel_browser_launch import BrowserLaunchError, launch_chromium


class ContentAwareCompositionTests(unittest.TestCase):
    def test_wraps_real_visible_long_text_and_detects_negative_control(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)
            self.assertEqual(generator.run(generator.DEFAULT_DESIGN,output,verify=False,
                                           presentation_mode='planned',design_critic_strategy='B'),0)
            source=(output/'preview.html').read_text(encoding='utf-8')
            try:
                with sync_playwright() as pw:
                    browser,_=launch_chromium(pw,headless=True,args=['--no-sandbox'])
                    try:
                        page=browser.new_page(viewport={'width':390,'height':900})
                        page.set_content(source,wait_until='load')
                        page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                        page.evaluate("()=>window.__MCEL_SET_VISUAL_FIXTURE('long_text')")
                        original=page.locator('[data-stress-fixture="long-text"]').text_content()
                        self.assertIn('Synthetic long command:',original)
                        measured=quality.browser_measure(page,153)
                        self.assertEqual(quality.feasibility(measured,'default'),[])
                        self.assertGreater(len(measured['wrapped_long_tokens']),0)
                        # Prove the measurement still detects actual escaping glyphs.
                        page.add_style_tag(content='''.record .semantic-record-field{
                            overflow-wrap:normal!important;white-space:nowrap!important;
                            max-width:none!important;min-width:max-content!important;}''')
                        negative=quality.browser_measure(page,153)
                        self.assertTrue(negative['longest_token_violations'] or negative['horizontal_overflow'])
                        page.evaluate("()=>window.__MCEL_SET_VISUAL_FIXTURE('default')")
                        self.assertEqual(page.locator('[data-stress-fixture="long-text"]').count(),0)
                    finally:
                        browser.close()
            except BrowserLaunchError:
                self.skipTest('Chromium unavailable')

    def test_actual_collection_width_controls_columns_including_font_growth(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)
            self.assertEqual(generator.run(generator.DEFAULT_DESIGN,output,verify=False,
                                           presentation_mode='planned',design_critic_strategy='C'),0)
            source=(output/'preview.html').read_text(encoding='utf-8')
            try:
                with sync_playwright() as pw:
                    browser,_=launch_chromium(pw,headless=True,args=['--no-sandbox'])
                    try:
                        for width,expected in [(320,1),(390,1),(768,2),(1440,2)]:
                            page=browser.new_page(viewport={'width':width,'height':900})
                            page.set_content(source,wait_until='load')
                            page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                            page.wait_for_timeout(80)
                            observed=page.evaluate('''()=>{
                                const list=document.querySelector('.record-list');
                                return {columns:Number(list.dataset.contentAwareColumns),
                                  minWidth:Number(list.dataset.minimumReadableWidth),
                                  widths:[...list.querySelectorAll('.record:not([hidden])')].map(r=>r.getBoundingClientRect().width),
                                  horizontal:document.documentElement.scrollWidth>innerWidth+1};}''')
                            self.assertEqual(observed['columns'],expected,(width,observed))
                            self.assertGreaterEqual(min(observed['widths']),240,(width,observed))
                            self.assertFalse(observed['horizontal'])
                            page.close()
                        page=browser.new_page(viewport={'width':550,'height':900})
                        page.set_content(source,wait_until='load')
                        page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                        page.wait_for_timeout(80)
                        page.evaluate('''()=>{
                            for(const r of document.querySelectorAll('.record')){
                                const n=parseFloat(getComputedStyle(r).fontSize);r.style.fontSize=(n*1.5)+'px';
                            }}''')
                        page.wait_for_timeout(150)
                        grow=page.evaluate('''()=>{const el=document.querySelector('.record-list');return {
                           columns:Number(el.dataset.contentAwareColumns),
                           minWidth:Number(el.dataset.minimumReadableWidth),
                           width:el.getBoundingClientRect().width}}''')
                        self.assertGreater(grow['minWidth'],240,grow)
                        self.assertEqual(grow['columns'],1,grow)
                    finally:
                        browser.close()
            except BrowserLaunchError:
                self.skipTest('Chromium unavailable')

    def test_independent_inventory_semantic_rows(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright unavailable')
        design=generator.yaml.safe_load(generator.DEFAULT_DESIGN.read_text(encoding='utf-8'))
        design=copy.deepcopy(design)
        design['application']['id']='warehouse-inventory-holdout'
        design['application']['name']='Warehouse Inventory'
        design['semantics']['entities']['inventory_item']={'identity':['sku'],'status':'proposed'}
        design['rows']['types']={'inventory-row':{
            'entity':'inventory_item','primary':['sku','name'],
            'summary':['quantity','location','restock_date','supplier','notes'],
            'actions':['inspect_inventory_item'],'status':'proposed'}}
        primary=next(r for r in design['hierarchy']['children'] if r['role']=='primary-content')
        primary['views']=[{'id':'inventory','title':'Inventory','sections':[{'id':'stock','row':'inventory-row'}]}]
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);spec=out/'design.yaml'
            spec.write_text(generator.yaml.safe_dump(design,sort_keys=False),encoding='utf-8')
            self.assertEqual(generator.run(spec,out/'generated',verify=False,
                                           presentation_mode='planned',design_critic_strategy='C'),0)
            source=(out/'generated'/'preview.html').read_text(encoding='utf-8')
            try:
                with sync_playwright() as pw:
                    browser,_=launch_chromium(pw,headless=True,args=['--no-sandbox'])
                    try:
                        for width in (320,390,1440):
                            page=browser.new_page(viewport={'width':width,'height':900})
                            page.set_content(source,wait_until='load')
                            page.wait_for_function('window.__MCEL_GENERATED_READY === true')
                            page.evaluate("()=>window.__MCEL_SET_VISUAL_FIXTURE('long_text')")
                            page.wait_for_timeout(100)
                            expected=page.locator('[data-stress-fixture="long-text"]').text_content()
                            measured=quality.browser_measure(page,quality.browser_measure(page,0)['component_count'])
                            self.assertTrue(expected.startswith('Synthetic long command:'),width)
                            self.assertEqual(quality.feasibility(measured,'default'),[],(width,measured))
                            self.assertGreaterEqual(min(measured['record_widths']),240,width)
                            page.close()
                    finally:
                        browser.close()
            except BrowserLaunchError:
                self.skipTest('Chromium unavailable')
