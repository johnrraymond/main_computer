"""Task Manager view-navigation and viewport regression against the real MCEL host.

The real application IR and operational renderer are used with inert read ports.
No backend service is started and no consequential intent is executed.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from main_computer.mcel_knowledge.workspace_plan import plan_workspace
from tools.mcel_phase2b_browser import launch_semantic_browser


VIEWS = {
    "server-processes": ("processes", "/applications/task-manager/server-processes"),
    "all-processes": ("all", "/applications/task-manager/all-processes"),
    "connections": ("connections", "/applications/task-manager/connections"),
    "cpu-gpu": ("hardware", "/applications/task-manager/hardware"),
    "operations": ("schedules", "/applications/task-manager/operations"),
}


def test_authored_navigation_is_not_an_ordinary_query_control():
    ir = compile_semantic_app("task-manager")
    plan = plan_workspace(ir)
    regions = {r["id"].split(".")[-1]: r for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    assert regions["views"]["purpose"] == "navigation"
    assert "query" not in regions
    assert not any(zone["id"] == "controls" for zone in plan["zones"])
    assert next(z for z in plan["zones"] if z["id"] == "navigator")["members"] == [regions["views"]["id"]]
    assert next(z for z in plan["zones"] if z["id"] == "workspace")["members"]
    # Every operational view retains its own query and associated result.
    members = {r["id"].split(".")[-1]: {m["ref"].split(".")[-1] for m in r["members"]}
               for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    assert "select-notebook" in members["views"]
    assert {"monitor-query", "process-list"} <= members["processes"]
    for region, form in (("all", "all-query"), ("connections", "connection-query"), ("hardware", "hardware-query")):
        assert form in members[region]


@pytest.mark.parametrize("width,height", [(390, 588), (680, 588), (1040, 768), (1272, 588)])
def test_visible_navigation_is_unique_and_primary_workspace_is_usable(width, height):
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Do not allow test interactions to contact any service.
            page.evaluate("""() => {window.fetch=async()=>({ok:false,status:503,
                json:async()=>({error:{code:'MCEL_TEST_INERT_PORT'}})});}""")
            page.set_content(render_semantic_page("task-manager", promoted=True))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            assert page.locator('[data-mcel-zone="controls"]').count() == 0
            assert page.locator('[data-mcel-purpose="navigation"] [data-mcel-mode-tabs]').count() == 1
            assert page.locator('[data-mcel-purpose="navigation"] [data-value]').count() == 5
            assert page.locator('[data-intent="intent:switch-view"] select').count() == 0
            assert page.locator('[data-mcel-region$=".processes"] form[data-intent="intent:refresh"]').count() == 1
            assert page.locator('[data-mcel-region$=".processes"] form').count() == 1
            assert page.locator('[data-mcel-workspace-subtabs] [role="tab"]').count() == 3
            geometry = page.evaluate("""() => {
                const layout=document.querySelector('[data-mcel-viewport-composed]');
                const workspace=layout.querySelector('[data-mcel-zone="workspace"]');
                const nav=layout.querySelector('[data-mcel-zone="navigator"]');
                const rect=el=>el.getBoundingClientRect();
                return {width:rect(workspace).width,height:rect(workspace).height,
                  navX:rect(nav).x,navRight:rect(nav).right,navBottom:rect(nav).bottom,
                  workX:rect(workspace).x,workTop:rect(workspace).top,
                  horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1};
            }""")
            assert not geometry["horizontalOverflow"]
            assert geometry["height"] >= (150 if width < 680 else 250 if width < 980 else 300)
            if width < 680:
                assert geometry["navBottom"] <= geometry["workTop"] + 2
            else:
                assert geometry["navRight"] <= geometry["workX"] + 2
            for name, (region, href) in VIEWS.items():
                control = page.locator('[data-mcel-purpose="navigation"] [data-value="'+name+'"]')
                assert control.get_attribute("href") == href
                control.click()
                assert page.evaluate("__mcelWorkbench.app.readState('state:view')") == name
                assert control.get_attribute("aria-current") == "page"
                assert page.locator('[data-mcel-region$=".'+region+'"]:visible').count() == 1
                if name == "operations":
                    assert page.locator('[data-mcel-workspace-subtabs] [role="tab"]').count() == 3
                    assert page.locator('[data-mcel-zone="tools"] [data-mcel-tool-tabs]').count() == 0
                assert page.locator('[data-mcel-purpose="navigation"] [aria-current="page"]').count() == 1
            assert errors == []
            page.close()
        finally:
            browser.close()


def test_navigation_preserves_browser_owned_modified_clicks():
    with sync_playwright() as pw:
        browser, _ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1272, "height": 588})
            page.set_content(render_semantic_page("task-manager", promoted=True))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            outcome = page.evaluate("""() => {
              const link=document.querySelector('[data-mcel-purpose="navigation"] [data-value="connections"]');
              const event=new MouseEvent('click',{bubbles:true,cancelable:true,ctrlKey:true,button:0});
              link.dispatchEvent(event);
              return {href:link.getAttribute('href'),prevented:event.defaultPrevented,
                 state:__mcelWorkbench.app.readState('state:view')};
            }""")
            assert outcome == {"href": "/applications/task-manager/connections",
                               "prevented": False, "state": "server-processes"}
            page.close()
        finally:
            browser.close()

