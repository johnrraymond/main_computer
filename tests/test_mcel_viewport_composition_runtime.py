"""Real Chromium witnesses for bounded semantic scroll owners across view changes.

These tests keep services inert and retain the production host and runtime DOM.
No front-end action is allowed to enlarge or scroll the MCEL root.
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_slh_application import render_slh_page
from tools import mcel_viewport_composition_oracle as oracle
from tools.mcel_phase2b_browser import launch_semantic_browser


def _open(page, html):
    page.evaluate('''() => {
      window.fetch=async()=>({ok:false,status:503,
        json:async()=>({error:{code:'MCEL_TEST_NO_SERVICE'}})});
      const storage={getItem:()=>null,setItem:()=>{},removeItem:()=>{},clear:()=>{}};
      Object.defineProperty(window,'localStorage',{value:storage,configurable:true});
      Object.defineProperty(window,'sessionStorage',{value:storage,configurable:true});
    }''')
    page.set_content(html,wait_until='load')
    page.wait_for_selector('[data-mcel-natural-layout], [data-mcel-viewport-composed]')


def _check(page):
    result=oracle.assess(page.evaluate(oracle.DOM_OBSERVATION))
    assert result['status']=='PASS',result
    return page.evaluate('''() => {
      const app=document.querySelector('#mcel-workbench');
      const root=app.querySelector('[data-mcel-region]');
      const owners=app.querySelectorAll('[data-mcel-zone="navigator"], [data-mcel-zone="workspace"]');
      return {scroll:window.scrollY, appScroll:app.scrollTop, rootScroll:root.scrollTop,
        regionIds:[...app.querySelectorAll('[data-mcel-region]')].map(e=>e.dataset.mcelRegion),
        owners:[...owners].filter(e=>e.parentElement===root || e.parentElement?.matches('[data-mcel-viewport-composed]')).length};
    }''')


def test_slh_view_change_preserves_regions_and_scroll_ownership():
    with sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            page=browser.new_page(viewport={'width':1040,'height':768})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            _open(page,render_slh_page())
            before=_check(page)
            page.get_by_role('button',name='Worker Bank').click()
            after=_check(page)
            assert before['regionIds']==after['regionIds']
            assert before['owners']==after['owners']==2
            assert after['scroll']==after['appScroll']==after['rootScroll']==0
            assert errors==[]
            page.close()
        finally:
            browser.close()


def test_task_manager_tab_switch_keeps_primary_workspace_bounded():
    with sync_playwright() as playwright:
        browser,_=launch_semantic_browser(playwright)
        try:
            page=browser.new_page(viewport={'width':1040,'height':768})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            _open(page,render_semantic_page('task-manager'))
            before=_check(page)
            page.get_by_role('tab',name='Scheduled operations and selected schedule').click()
            after=_check(page)
            assert before['regionIds']==after['regionIds']
            assert before['owners']==after['owners']==2
            assert after['scroll']==after['appScroll']==after['rootScroll']==0
            assert errors==[]
            page.close()
        finally:
            browser.close()
