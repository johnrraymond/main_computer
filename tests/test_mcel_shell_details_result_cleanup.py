"""Canonical shell chrome, delegated app identity and semantic layout ownership.

Browser probes use native semantic pages and inert browser-local state. No
service requests or consequential actions are performed.
"""
from __future__ import annotations

import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_applications_shell_host import render_applications_shell_candidate
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_chat_console_host import render_chat_console_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page, compile_semantic_app
from main_computer.mcel_knowledge.workspace_plan import plan_workspace


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None,
                              args=['--no-sandbox'])
        try:
            yield b
        finally:
            b.close()


def _page(browser, markup, width=1280):
    p=browser.new_page(viewport={'width':width,'height':800})
    p.set_content(markup)
    return p


def test_shell_carries_one_heading_without_redundant_controls(browser):
    page=_page(browser, render_applications_shell_candidate(canonical_promoted=True))
    try:
        assert page.locator('body').get_attribute('data-mcel-shell-ready')=='true'
        assert page.locator('#mcel-shell-masthead .mcel-shell-head-right').count()==0
        assert page.locator('#mcel-shell-open-native').count()==0
        assert page.locator('.mcel-shell-toolbar').count()==0
        assert page.locator('.mcel-shell-proof').count()==0
        assert page.locator('#mcel-shell-open-apps').count()==0
        assert page.locator('.mcel-shell-apps-trigger').count()==0
        panel=page.locator('#mcel-shell-launcher-panel')
        assert panel.count()==1
        page.mouse.move(20,360)
        page.wait_for_timeout(250)  # Let the existing hover rail finish expanding.
        assert abs(panel.bounding_box()['x'])<=3
        page.keyboard.press('Escape')
        page.wait_for_timeout(250)  # Allow the rail to retract.
        assert panel.get_attribute('data-open')=='false'
        assert abs(panel.bounding_box()['x']+panel.bounding_box()['width']-56)<=3
        assert page.locator('#mcel-shell-active-title').count()==1
    finally:
        page.close()



def test_no_hamburger_on_any_shell_application(browser):
    page=_page(browser,render_applications_shell_candidate(canonical_promoted=True))
    try:
        page.evaluate('history.pushState = () => {}')
        apps=page.evaluate('window.__mcelApplicationsShell.availableApplications()')
        assert len(apps)==21
        for app in apps:
            assert page.evaluate('(id)=>window.__mcelApplicationsShell.select(id,true)',app)
            assert page.locator('#mcel-shell-active-title').inner_text()
            assert page.locator('#mcel-shell-open-apps, .mcel-shell-apps-trigger').count()==0
            assert page.locator('#mcel-shell-launcher-panel').count()==1
    finally:
        page.close()


def test_calculator_primary_result_precedes_expression(browser):
    page=_page(browser, render_expanded_page())
    try:
        workspace=page.locator('[data-mcel-zone="workspace"]')
        result=workspace.locator('[data-mcel-purpose="results"][data-mcel-priority="primary"]')
        expression=workspace.locator('[data-mcel-purpose="input"][data-mcel-priority="primary"]')
        assert result.count()==expression.count()==1
        assert result.evaluate('(el)=>el.parentElement.firstElementChild===el')
        assert result.bounding_box()['y']<expression.bounding_box()['y']
        inspector=page.locator('[data-mcel-natural-scroller="inspection"]')
        assert inspector.count()==1
        assert inspector.locator('[data-mcel-activity]').count()==1
        assert inspector.bounding_box()['x']>=workspace.bounding_box()['x']+workspace.bounding_box()['width']-2
    finally:
        page.close()


def test_embedded_expert_delegates_title_to_parent_shell(browser):
    page=_page(browser,'<iframe id="mcel-shell-frame" title="Calculator"></iframe>')
    try:
        frame=page.frames[-1]
        frame.evaluate('''() => {
          const data=new Map();Object.defineProperty(window,'localStorage',{configurable:true,value:{
            getItem:k=>data.get(String(k))??null,setItem:(k,v)=>data.set(String(k),String(v)),
            removeItem:k=>data.delete(String(k)),clear:()=>data.clear()}});
        }''')
        frame.set_content(render_expanded_page())
        assert frame.locator('[data-mcel-application-title]').count()==0
        assert frame.locator('[data-mcel-zone="navigator"] [data-mcel-mode-tabs] button').count()==4
        assert frame.locator('#mcel-expanded-diagnostic').inner_text()=='Semantic Calculator ready'
    finally:
        page.close()


def test_task_manager_inspection_and_consequential_workflows_separate(browser):
    plan=plan_workspace(compile_semantic_app('task-manager'))
    zones={zone['id']:zone for zone in plan['zones']}
    assert zones['details']['kind']=='inspector'
    assert [id.rsplit('.',1)[-1] for id in zones['details']['members']]==['selected']
    assert zones['tools']['ladder']['wide']=='dock-bottom'
    assert zones['details']['ladder']['wide']=='dock-end'
    assert all(p=='inspector-after-details' for p in zones['status']['ladder'].values())
    for width in (390,800,1280):
        page=_page(browser,render_semantic_page('task-manager'),width)
        try:
            assert page.locator('#mcel-workbench-status').inner_text()=='Semantic candidate ready'
            work=page.locator('[data-mcel-zone="workspace"]')
            tools=page.locator('[data-mcel-zone="tools"]')
            details=page.locator('[data-mcel-zone="details"]')
            assert work.count()==tools.count()==details.count()==1
            assert details.locator('[data-mcel-purpose="inspection"]').count()==1
            assert details.locator('[data-mcel-zone="status"] [data-mcel-purpose="status"]').count()==1
            assert tools.locator('form[data-intent="intent:create-schedule"]').count()==1
            assert tools.locator('[data-mcel-tool-tabs] [role="tab"]').count()==3
            assert work.bounding_box()['height']>=80
            assert tools.bounding_box()['y']>=work.bounding_box()['y']+work.bounding_box()['height']-2
            if width>=980:
                assert details.bounding_box()['x']>=work.bounding_box()['x']+work.bounding_box()['width']-2
            assert details.evaluate('(el)=>getComputedStyle(el).overflowY')=='auto'
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
        finally:
            page.close()


def test_chat_console_omits_redundant_heading_only_inside_shell(browser):
    page=_page(browser,'<iframe id="mcel-shell-frame" title="Chat Console"></iframe>')
    try:
        frame=page.frames[-1]
        frame.set_content(render_chat_console_page(promoted=True))
        assert frame.locator('body').get_attribute('data-mcel-shell-embedded')=='true'
        assert not frame.locator('.chat-console-header > div:first-child').is_visible()
        assert frame.locator('#chat-console-save-status').is_visible()
    finally:
        page.close()
