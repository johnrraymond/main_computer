"""Bounded, inert Chromium proof for semantic Operations workarea subtabs.

No server is started, no requests are sent, and no consequential action is executed.
"""
from __future__ import annotations

import copy

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_knowledge.workspace_plan import plan_workspace, verify_workspace_plan
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def test_authoring_keeps_operations_as_three_subviews_of_one_mode():
    ir = compile_semantic_app('task-manager')
    plan = plan_workspace(ir)
    verify_workspace_plan(ir,plan)
    regions = {r['id'].split('.')[-1]: r for r in ir['surfaces'][0]['interfaceIntent']['regions']}
    workspace = next(zone for zone in plan['zones'] if zone['id'] == 'workspace')
    assert not any(zone['id'] == 'details' for zone in plan['zones'])
    assert next(z for z in plan['zones'] if z['id'] == 'tools')['members'] == [regions['selected']['id']]
    assert len(workspace['modeSubtabs']) == 1
    subtabs = workspace['modeSubtabs'][0]
    assert subtabs['equals'] == 'operations'
    assert subtabs['members'] == [regions[r]['id'] for r in ('schedules','assistant','operations')]
    assert all(regions[r]['visibleWhen']['equals'] == 'operations' for r in ('schedules','assistant','operations'))
    assert set(subtabs['members']) <= set(workspace['members'])
    # Binding and effect identity are not rewritten by the layout planner.
    assert {'intent:create-schedule','intent:task-ai','intent:terminate-process'} <= {i['id'] for i in ir['intents']}
    assert not any(zone['kind'] == 'tab-group' for zone in plan['zones'])


def test_workspace_planner_rejects_mixed_subtab_membership():
    ir=copy.deepcopy(compile_semantic_app('task-manager'))
    regions=ir['surfaces'][0]['interfaceIntent']['regions']
    assistant=next(r for r in regions if r['id'].endswith('.assistant'))
    assistant.pop('subviewGroup')
    with pytest.raises(ValueError,match='complete conditional mode'):
        plan_workspace(ir)


@pytest.mark.parametrize('width,height',[(390,588),(680,588),(1040,768),(1272,588)])
def test_operational_subtabs_own_full_primary_workspace(width,height):
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':width,'height':height})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate("""() => {window.fetch=async()=>({ok:false,status:503,
                json:async()=>({error:{code:'MCEL_TEST_INERT_PORT'}})});}""")
            page.set_content(render_semantic_page('task-manager',promoted=True))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            assert page.locator('[data-mcel-zone="tools"] [data-mcel-tool-tabs]').count() == 0
            assert page.locator('[data-mcel-purpose="navigation"] [data-value]').count() == 5
            navigation=page.locator('[data-mcel-purpose="navigation"] [data-value="operations"]')
            assert navigation.get_attribute('href') == '/applications/task-manager/operations'
            navigation.click()
            assert page.evaluate("__mcelWorkbench.app.readState('state:view')") == 'operations'
            assert navigation.get_attribute('aria-current') == 'page'
            tabs=page.locator('[data-mcel-workspace-subtabs] [role="tab"]')
            assert tabs.count()==3
            headings=('Scheduled operations and selected schedule',
                      'AI operational analysis (explicit request)',
                      'Governed operations and server status')
            for index,heading in enumerate(headings):
                tabs.nth(index).click()
                assert tabs.nth(index).get_attribute('aria-selected') == 'true'
                assert page.locator('[data-mcel-workspace-subview]:visible').count() == 1
                panel=page.locator('[data-mcel-workspace-subview]:visible')
                assert panel.locator('h2').first.inner_text().lower()==heading.lower()
                assert panel.locator('[data-mcel-region]').count()==1
                metrics=page.evaluate('''() => {
                  const w=document.querySelector('[data-mcel-zone="workspace"]');
                  const p=w.querySelector('[data-mcel-workspace-subview]:not([hidden])');
                  const a=w.getBoundingClientRect(),b=p.getBoundingClientRect();
                  return {workspaceHeight:a.height,panelHeight:b.height,
                    panelBottom:b.bottom,workspaceBottom:a.bottom,
                    panelWidth:b.width,workspaceWidth:a.width,
                    outerOverflow:w.scrollHeight-w.clientHeight,
                    pageWidth:document.documentElement.scrollWidth};
                }''')
                assert metrics['workspaceHeight'] >= 150
                assert metrics['panelHeight'] >= 40
                assert metrics['workspaceBottom']-metrics['panelBottom'] <= 25
                assert metrics['panelWidth'] <= metrics['workspaceWidth']+2
                assert metrics['outerOverflow'] <= 2
                assert metrics['pageWidth'] <= width+2
            # Re-entering Operations preserves the currently selected subview.
            page.locator('[data-mcel-purpose="navigation"] [data-value="server-processes"]').click()
            assert page.locator('[data-mcel-region$=".processes"]:visible').count() == 1
            assert page.locator('[data-mcel-workspace-subtabs]:visible').count() == 0
            navigation.click()
            assert tabs.nth(2).get_attribute('aria-selected') == 'true'
            assert page.locator('[data-mcel-region$=".operations"]:visible').count() == 1
            assert errors == []
            page.close()
        finally:
            browser.close()


def test_operations_deeplink_selects_full_workspace_without_data_access():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page(viewport={'width':1040,'height':768})
            page.set_content(render_semantic_page('task-manager',promoted=True,initial_view='operations'))
            page.wait_for_selector('[data-mcel-viewport-composed]')
            assert page.evaluate("__mcelWorkbench.app.readState('state:view')") == 'operations'
            assert page.locator('[data-mcel-region$=".schedules"]:visible').count()==1
            assert page.locator('[data-mcel-zone="tools"] [data-mcel-tool-tabs]').count()==0
            page.close()
        finally:
            browser.close()
