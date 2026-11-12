"""Shared MCEL control composition: actions wrap, keypads stay bounded.

The independent browser oracle measures real rendered controls rather than
assuming that the existence of a grid or flexbox makes it usable. These tests
are isolated: the SLH endpoint is mocked and no privileged operation is run.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_semantic_generated_host import render_generated_page
from main_computer.mcel_slh_application import render_slh_page
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_viewport_composition_oracle import DOM_OBSERVATION, assess


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser, _ = launch_semantic_browser(playwright)
        try:
            yield browser
        finally:
            browser.close()


def open_page(browser, html: str, width: int):
    page = browser.new_page(viewport={"width": width, "height": 800})
    page.evaluate("""() => {
      const data = new Map();
      const memory = {getItem:k=>data.get(String(k))??null,
        setItem:(k,v)=>data.set(String(k),String(v)),
        removeItem:k=>data.delete(String(k)),clear:()=>data.clear(),
        key:i=>[...data.keys()][i]??null,get length(){return data.size;}};
      Object.defineProperty(window,'localStorage',{configurable:true,value:memory});
      window.fetch=async()=>({ok:false,status:503,
        json:async()=>({error:{code:'ISOLATED_NO_SERVICE'}})});
    }""")
    page.set_content(html, wait_until="domcontentloaded")
    return page


@pytest.mark.parametrize("width", [390, 1040, 1440])
def test_configuration_workspace_has_no_synthetic_refresh_toolbar(browser, width):
    page = open_page(browser, render_slh_page(), width)
    try:
        # Stage 1 removes the obsolete generated refresh toolbar entirely.
        assert page.locator('[data-mcel-action-flow]').count() == 0
        draft = page.locator('form[data-intent="intent:configure-and-enable-organization"]')
        assert draft.count() == 1
        assert draft.get_by_role('button',name='Configure ORG-SLH').is_disabled()  # No LMS identity observed
        assert page.locator('button[data-intent="intent:disable-organization"]').count() == 1
        assert assess(page.evaluate(DOM_OBSERVATION))['status'] == 'PASS'
        assert page.locator('#mcel-workbench').evaluate('e=>e.scrollHeight==e.clientHeight')
    finally:
        page.close()


@pytest.mark.parametrize("width", [390, 1040, 1440])
def test_generated_monitor_view_selector_retains_semantics(browser, width):
    page = open_page(browser, render_generated_page(), width)
    try:
        assert page.locator('[data-mcel-action-flow]').count() == 0
        assert page.locator('[data-mcel-mode-tabs] button').count() == 4
        assert assess(page.evaluate(DOM_OBSERVATION))['status'] == 'PASS'
        page.locator('[data-mcel-mode-tabs] button[data-value="connections"]').click()
        assert page.locator('[data-mcel-mode-tabs] button[data-value="connections"]').get_attribute('aria-pressed') == 'true'
    finally:
        page.close()


@pytest.mark.parametrize("width", [390, 1040, 1440])
def test_calculator_keypad_is_bounded_but_still_operates(browser, width):
    page = open_page(browser, render_expanded_page(), width)
    try:
        assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
        obs = page.evaluate(DOM_OBSERVATION)
        assert assess(obs)['status'] == 'PASS', assess(obs)['failures']
        pad, = obs['controlComposition']['keypads']
        assert pad['keyCount'] == 21 and pad['fourColumns']
        assert pad['width'] <= 422 and pad['maxKeyWidth'] <= 121
        assert pad['submitWidth'] <= 422 and pad['submitAligned']
        keys = page.locator('[data-mcel-expression-pad]')
        for action in ('7', '+', '8', 'submit'):
            keys.locator('button[data-pad-action="'+action+'"]').click()
        page.locator('[data-state="state:arithmetic-result"]').get_by_text('15').wait_for()
        assert page.locator('form[data-intent="intent:evaluate"] input[name="expression"]').input_value() == '7+8'
        # Mode selection and its navigation placement remain unchanged.
        page.locator('[data-mcel-mode-tabs] button[data-value="graphing"]').click()
        assert page.evaluate('window.__mcelExpanded.app.readState("state:mode")') == 'graphing'
    finally:
        page.close()


def test_negative_control_rejects_stretched_keypad(browser):
    # The legacy SLH toolbar no longer exists. Keep the independent keypad
    # negative control rather than manufacturing nonexistent SLH controls.
    page = open_page(browser, render_expanded_page(), 1440)
    try:
        assert assess(page.evaluate(DOM_OBSERVATION))['status'] == 'PASS'
        page.add_style_tag(content='''
          [data-mcel-expression-pad]{max-width:none!important;}
          form[data-mcel-composition="expression-workbench"] > button[type="submit"]{
            max-width:none!important;justify-self:stretch!important;
          }''')
        result = assess(page.evaluate(DOM_OBSERVATION))
        assert result['status'] == 'FAIL'
        assert 'keypad-bounded-width' in result['failures']
        assert 'keypad-four-regular-columns' in result['failures']
        assert 'keypad-submit-bounded' in result['failures']
    finally:
        page.close()


def test_configuration_task_cannot_capture_navigation_or_implicitly_execute_lifecycle(browser):
    page = open_page(browser, render_slh_page(), 1040)
    try:
        rail = page.locator('[data-mcel-zone="navigator"]')
        assert rail.locator('[data-mcel-action-flow]').count() == 0
        assert rail.locator('[data-mcel-purpose="navigation"] [data-collection]').count() == 1
        assert page.locator('[data-mcel-action-flow]').count() == 0
        assert page.locator('button[data-intent="intent:login-administrator"]').count() == 0
        assert page.locator('button[data-intent="intent:activate-organization"]').count() == 0
        assert page.locator('[data-mcel-purpose="task"] form[data-intent="intent:configure-and-enable-organization"]').count() == 1
        assert page.locator('[data-mcel-purpose="commands"] [data-intent="intent:disable-organization"]').count() == 1
        assert page.locator('form[data-intent="intent:configure-and-enable-organization"] button[type="submit"]').is_disabled()
        assert page.locator('[data-mcel-reference="selected"]').count() == 0  # no observed identity
        # The lifecycle region and its control persist; only execution is disabled.
        assert page.locator('[data-mcel-purpose="commands"]').is_visible()
        assert page.locator('[data-mcel-purpose="commands"] [data-intent="intent:disable-organization"]').is_visible()
        assert page.locator('[data-mcel-purpose="commands"] [data-intent="intent:disable-organization"]').is_disabled()
    finally:
        page.close()
