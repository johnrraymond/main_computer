"""Canonical view navigation must preserve intent binding for MCEL Theme.

Uses the actual promoted MCEL host and an inert browser; no backend actions.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


VIEWS = (
    ("server-processes", "/applications/task-manager/server-processes"),
    ("all-processes", "/applications/task-manager/all-processes"),
    ("connections", "/applications/task-manager/connections"),
    ("cpu-gpu", "/applications/task-manager/hardware"),
    ("operations", "/applications/task-manager/operations"),
)


@pytest.mark.parametrize("width,height", [(390, 588), (680, 588), (1040, 768), (1272, 588)])
def test_task_manager_navigation_links_receive_default_theme_and_keep_navigation(width, height):
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(render_semantic_page("task-manager", promoted=True))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            status = page.evaluate("""() => {
              const root=document.querySelector('[data-mcel-semantic-operational]');
              return {color:__mcelWorkbench.app.colorPresentation(),
                presentation:McelUnifiedPresentationController.operationalReport(root),
                diagnostic:window.__mcelPresentationDiagnostic || null,
                controls:[...root.querySelectorAll('[data-mcel-purpose="navigation"] [data-value]')]
                  .map(x=>({value:x.getAttribute('data-value'),
                    href:x.getAttribute('href'),intent:x.getAttribute('data-intent'),
                    role:x.getAttribute('data-mcel-color-role'),
                    state:x.getAttribute('data-mcel-color-state')}))};
            }""")
            assert status["diagnostic"] is None, status
            assert status["color"]["status"] == "applied", status
            assert status["color"]["theme"] == "theme-machine", status
            assert status["presentation"]["status"] == "applied", status
            assert status["presentation"]["steps"] == {
                "theme": "applied", "form": "applied", "chrome": "applied"
            }, status
            assert [(c["value"], c["href"]) for c in status["controls"]] == list(VIEWS)
            assert all(c["intent"] == "intent:switch-view" for c in status["controls"])
            assert all(c["role"] == "navigation.item" and c["state"] for c in status["controls"])
            for view, _ in VIEWS:
                link = page.locator(f'[data-mcel-purpose="navigation"] [data-value="{view}"]')
                link.click()
                assert page.evaluate("__mcelWorkbench.app.readState('state:view')") == view
                assert link.get_attribute("aria-current") == "page"
            assert not errors, errors
            page.close()
        finally:
            browser.close()


def test_navigation_without_canonical_routes_still_binds_each_generated_button():
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        try:
            page = browser.new_page()
            page.set_content(render_semantic_page("task-manager", promoted=False))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            buttons = page.locator('[data-mcel-purpose="navigation"] button[data-value]')
            assert buttons.count() == len(VIEWS)
            assert all(buttons.nth(i).get_attribute('data-intent') == 'intent:switch-view'
                       for i in range(len(VIEWS)))
            buttons.nth(2).click()
            assert page.evaluate("__mcelWorkbench.app.readState('state:view')") == 'connections'
            page.close()
        finally:
            browser.close()


@pytest.mark.parametrize("application", ["file-manager", "code-editor"])
def test_other_promoted_applications_keep_all_default_presentation_laws(application):
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        try:
            page = browser.new_page()
            page.set_content(render_semantic_page(application, promoted=True))
            page.wait_for_selector('[data-mcel-semantic-operational]')
            status = page.evaluate("""() => {
              const root=document.querySelector('[data-mcel-semantic-operational]');
              return {theme:__mcelWorkbench.app.colorPresentation(),
                report:McelUnifiedPresentationController.operationalReport(root)};
            }""")
            assert status['theme']['status'] == 'applied', status
            assert status['report']['steps'] == {
                'theme':'applied', 'form':'applied', 'chrome':'applied'
            }, status
            page.close()
        finally:
            browser.close()


@pytest.mark.parametrize("promoted", [False, True])
def test_view_switch_does_not_paint_the_first_inactive_navigation_item_as_completed(promoted):
    """The source intent's operation status belongs to its hidden adapter,
    never to the first visible destination after changing to another view.
    """
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1272, "height": 588})
            page.set_content(render_semantic_page("task-manager", promoted=promoted))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            for view in ("operations", "connections", "cpu-gpu", "server-processes", "operations"):
                page.locator(f'[data-mcel-purpose="navigation"] [data-value="{view}"]').click()
                page.mouse.move(1200, 500)  # No hovered/focused navigation exception.
                state = page.evaluate("""() => {
                  const root=document.querySelector('[data-mcel-semantic-operational]');
                  const links=[...root.querySelectorAll('[data-mcel-purpose="navigation"] [data-value]')];
                  const source=root.querySelector('select[data-mcel-navigation-state-adapter][data-intent="intent:switch-view"]');
                  return {view:__mcelWorkbench.app.readState('state:view'),
                    adapter:!!source && source.closest('[hidden]')!==null &&
                        getComputedStyle(source.closest('[hidden]')).display==='none',
                    items:links.map(el=>{
                      const css=getComputedStyle(el);
                      return {value:el.dataset.value,selected:el.getAttribute('aria-pressed')==='true',
                        border:css.borderTopColor,background:css.backgroundColor,
                        role:el.getAttribute('data-mcel-color-role'),
                        state:el.getAttribute('data-mcel-color-state')};
                    })};
                }""")
                assert state['view']==view, state
                assert state['adapter'], state
                assert len(state['items'])==5, state
                active=[item for item in state['items'] if item['selected']]
                inactive=[item for item in state['items'] if not item['selected']]
                assert [item['value'] for item in active]==[view], state
                assert all((item['border'],item['background'],item['state']) ==
                           (inactive[0]['border'],inactive[0]['background'],inactive[0]['state'])
                           for item in inactive), state
                assert (active[0]['border'],active[0]['background']) != (
                    inactive[0]['border'],inactive[0]['background']), state
                if promoted:
                    assert all(item['role']=='navigation.item' for item in state['items']), state
            page.close()
        finally:
            browser.close()
