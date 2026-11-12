"""Application identity belongs to the semantic navigation rail, never a host header.

Runs the real generated hosts and browser runtime, not screenshot-only proxies.
Services are replaced by inert observations; administrator actions are never run.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_semantic_generated_host import render_generated_page, render_p3_generated_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_slh_network_host import render_network_slh_page
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_viewport_composition_oracle import DOM_OBSERVATION, assess

HOSTS = {
    "slh-local": render_slh_page,
    "slh-network": render_network_slh_page,
    "file-manager": lambda: render_semantic_page("file-manager"),
    "task-manager": lambda: render_semantic_page("task-manager"),
    "code-editor": lambda: render_semantic_page("code-editor"),
    "calculator": render_expanded_page,
    "generated-monitor": render_generated_page,
    "task-manager-p3": render_p3_generated_page,
}

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser, _ = launch_semantic_browser(playwright)
        try:
            yield browser
        finally:
            browser.close()


def _page(browser, html, width):
    page = browser.new_page(viewport={"width":width, "height":800})
    page.evaluate('''() => {
      const data=new Map();const memory={getItem:k=>data.get(String(k))??null,
        setItem:(k,v)=>data.set(String(k),String(v)),removeItem:k=>data.delete(String(k)),
        clear:()=>data.clear(),key:i=>[...data.keys()][i]??null,
        get length(){return data.size;}};
      Object.defineProperty(window,'localStorage',{configurable:true,value:memory});
      Object.defineProperty(window,'sessionStorage',{configurable:true,value:memory});
      window.fetch=async()=>({ok:false,status:503,json:async()=>({error:{code:'TEST_NO_SERVICE'}})});
    }''')
    page.set_content(html,wait_until='domcontentloaded')
    return page


@pytest.mark.parametrize("name", tuple(HOSTS))
@pytest.mark.parametrize("width", [390,1040,1440])
def test_application_title_is_derived_from_ir_and_owned_by_compact_navigation(browser,name,width):
    page = _page(browser, HOSTS[name](),width)
    try:
        status=page.locator('#mcel-workbench-status,#mcel-expanded-diagnostic').inner_text()
        assert 'refused:' not in status.lower() or name=='slh-local' and 'observation refused' in status.lower(), status
        title=page.locator('[data-mcel-zone="navigator"] [data-mcel-application-title]')
        assert title.count()==1
        ir=page.evaluate('''() => {
          const script=document.querySelector('#mcel-generated-data,#mcel-workbench-ir,#mcel-expanded-ir');
          const payload=JSON.parse(script.textContent);return payload.ir||payload;
        }''')
        assert title.inner_text()==ir['application']['title']
        assert page.locator('body > header').count()==0
        result=assess(page.evaluate(DOM_OBSERVATION))
        assert result['status']=='PASS',result
        assert all(result['checks'][check] for check in (
            'application-title-in-navigation','application-title-compact',
            'no-separate-application-header','workspace-starts-near-application-top'))
    finally:
        page.close()


def test_slh_target_links_and_session_remain_in_navigation(browser):
    page=_page(browser,render_slh_page(),1040)
    try:
        nav=page.locator('[data-mcel-zone="navigator"]')
        assert nav.locator('.slh-target-tabs a').count()==2
        assert nav.locator('.slh-target-tabs a').evaluate_all('els=>els.map(e=>e.getAttribute("href"))')==[
            '/applications/slh/local','/applications/slh/network']
        assert nav.locator('#mcel-slh-auth-status').inner_text()=='Organization session not established'
        # Configuration-first local SLH has no administrative views to switch.
        assert nav.locator('[data-mcel-mode-tabs] a[href]').count()==0
        assert page.locator('form[data-intent="intent:configure-and-enable-organization"]').count()==1
        assert page.locator('button[data-intent="intent:login-administrator"]').count()==0
        assert page.locator('body > header, header > nav').count()==0
    finally:
        page.close()


def test_calculator_mode_navigation_still_works_without_document_header(browser):
    page=_page(browser,render_expanded_page(),1040)
    try:
        nav=page.locator('[data-mcel-zone="navigator"]')
        assert nav.locator('[data-mcel-mode-tabs] button').all_inner_texts()==[
            'Basic','Graphing','Symbolic','Help']
        nav.get_by_role('button',name='Graphing').click()
        assert page.evaluate('window.__mcelExpanded.app.readState("state:mode")')=='graphing'
        assert page.locator('form[data-intent="intent:draw-graph"]').is_visible()
        assert page.locator('body > header').count()==0
    finally:
        page.close()


def test_oracle_refuses_reintroduced_full_width_header_or_missing_rail_title(browser):
    page=_page(browser,render_slh_page(),1040)
    try:
        assert assess(page.evaluate(DOM_OBSERVATION))['status']=='PASS'
        page.evaluate('''() => {const h=document.createElement('header');h.textContent='Oversized header';
          document.body.prepend(h);}''')
        failure=assess(page.evaluate(DOM_OBSERVATION))
        assert 'no-separate-application-header' in failure['failures'],failure
        page.locator('body > header').evaluate('(e)=>e.remove()')
        page.locator('[data-mcel-application-title]').evaluate('(e)=>e.remove()')
        failure=assess(page.evaluate(DOM_OBSERVATION))
        assert 'application-title-in-navigation' in failure['failures'],failure
    finally:
        page.close()
