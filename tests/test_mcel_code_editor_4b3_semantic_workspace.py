"""4B.3: native Code Editor document-workbench layout without new operation authority."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from main_computer.mcel_knowledge.workspace_plan import plan_workspace, verify_workspace_plan
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance, _ = launch_semantic_browser(playwright)
        try:
            yield instance
        finally:
            instance.close()


def page_for(browser, width=1380):
    page = browser.new_page(viewport={"width": width, "height": 820})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_content(render_semantic_page("code-editor", ROOT, promoted=True))
    assert page.locator("#mcel-workbench-status").inner_text() == "Semantic application ready"
    assert page.locator("[data-mcel-document-workbench]").count() == 1
    assert not failures, failures
    return page, failures


def test_semantic_preferences_are_canonical_and_operational_count_is_unchanged():
    ir = compile_semantic_app("code-editor", ROOT)
    plan = plan_workspace(ir)
    verify_workspace_plan(ir, plan)
    spec = plan["documentWorkbench"]
    assert spec["schema"] == "mcel.document-workbench.v1"
    assert [x.split(".")[-1] for x in spec["navigation"]] == ["navigation"]
    assert [x.split(".")[-1] for x in spec["companion"]] == ["aider", "documentation"]
    assert [x.split(".")[-1] for x in spec["bottom"]] == ["review", "studio"]
    assert [x.split(".")[-1] for x in spec["status"]] == ["status"]
    assert len(ir["intents"]) == 39
    assert len(ir["capabilities"]) == 13
    assert len(ir["effects"]) == 36
    assert len(ir["surfaces"][0]["nodes"]) == 58
    assert ir["layouts"] == []


def test_source_bound_plan_refuses_tampering_and_conflicting_placement():
    ir = compile_semantic_app("code-editor", ROOT)
    plan = plan_workspace(ir)
    changed = copy.deepcopy(plan)
    changed["documentWorkbench"]["bottom"].append(changed["documentWorkbench"]["companion"].pop())
    with pytest.raises(ValueError, match="disagrees"):
        verify_workspace_plan(ir, changed)
    changed_ir = copy.deepcopy(ir)
    sections = changed_ir["surfaces"][0]["interfaceIntent"]["regions"]
    next(x for x in sections if x["id"].endswith(".review"))["preferences"]["side"] = "start"
    with pytest.raises(ValueError, match="conflict"):
        plan_workspace(changed_ir)


def test_other_semantic_app_plans_are_unmodified():
    for app in ("file-manager", "task-manager"):
        plan = plan_workspace(compile_semantic_app(app, ROOT))
        assert "documentWorkbench" not in plan
        assert "mcel-semantic-document-workbench.js" not in render_semantic_page(app, ROOT)


def test_live_page_mounts_source_bound_layout_without_duplicate_nodes(browser):
    page, failures = page_for(browser)
    try:
        info = page.evaluate("""() => {
          const root=document.querySelector('[data-mcel-document-workbench]');
          const bounds=key=>{
            const r=root.querySelector(key).getBoundingClientRect();
            return {x:r.x,y:r.y,width:r.width,height:r.height};
          };
          return {document:bounds('[data-mcel-document-primary]'),
            explorer:bounds('[data-mcel-document-explorer]'),
            companion:bounds('[data-mcel-document-companion]'),
            bottom:bounds('[data-mcel-document-bottom]'),
            rail:bounds('[data-mcel-document-activity-rail]'),
            status:bounds('[data-mcel-document-status]'),
            surface:bounds('[data-mcel-document-surface]'),
            regions:root.querySelectorAll('[data-mcel-region]').length,
            openTabs:root.querySelectorAll('[data-mcel-document-tabs]').length,
            valid:!!window.__mcelWorkbench?.app,
            pageOverflow:document.documentElement.scrollWidth>innerWidth};
        }""")
        assert info["valid"] and not info["pageOverflow"], info
        assert info["regions"] == 7 and info["openTabs"] == 1, info
        assert info["rail"]["width"] <= 48 and info["explorer"]["x"] < info["document"]["x"] < info["companion"]["x"]
        assert info["document"]["height"] > 250 and info["bottom"]["height"] >= 100
        assert info["surface"]["width"] > 300 and info["surface"]["height"] > 180
        assert info["status"]["y"] > info["document"]["y"]
        assert not failures, failures
    finally:
        page.close()


def test_tabs_collapsing_preserves_bound_nodes_and_operation_contract(browser):
    page, failures = page_for(browser)
    try:
        page.evaluate("""() => {
          window.__originalDocumentEditor=document.querySelector('[data-mcel-document-editor]');
          window.__originalAider=document.querySelector('[data-mcel-region$=".aider"]');
          window.__originalStudio=document.querySelector('[data-mcel-region$=".studio"]');
        }""")
        page.locator('[data-mcel-document-tablist="companion"] [role="tab"]').nth(1).click()
        assert page.locator('[data-mcel-document-tablist="companion"] [role="tab"]').nth(1).get_attribute('aria-selected') == 'true'
        page.locator('[data-mcel-document-tablist="bottom"] [role="tab"]').nth(1).click()
        assert page.locator('[data-mcel-document-tablist="bottom"] [role="tab"]').nth(1).get_attribute('aria-selected') == 'true'
        page.locator('[data-mcel-document-toggle="explorer"]').click()
        assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-explorer-open') == 'false'
        page.locator('[data-mcel-document-activity="explorer"]').click()
        assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-explorer-open') == 'true'
        page.locator('[data-mcel-document-toggle="bottom"]').click()
        assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-bottom-open') == 'false'
        page.locator('[data-mcel-document-activity="bottom"]').first.click()
        assert page.locator('[data-mcel-document-workbench]').get_attribute('data-mcel-bottom-open') == 'true'
        assert page.evaluate("""() =>
          window.__originalDocumentEditor.isConnected &&
          window.__originalDocumentEditor===document.querySelector('[data-mcel-document-editor]') &&
          window.__originalAider.isConnected && window.__originalStudio.isConnected &&
          document.querySelectorAll('[data-mcel-region]').length===8""")
        assert not failures, failures
    finally:
        page.close()




def test_pointer_drag_resizes_explorer_without_remounting_monaco(browser):
    page, failures = page_for(browser)
    try:
        page.evaluate("""() => {window.__documentBefore=document.querySelector('[data-mcel-document-editor]');}""")
        handle=page.locator('[data-mcel-document-splitter="explorer"]')
        start=int(handle.get_attribute('aria-valuenow'))
        bounds=handle.bounding_box()
        x=bounds['x']+bounds['width']/2
        y=bounds['y']+min(50,bounds['height']/2)
        page.mouse.move(x,y)
        page.mouse.down()
        page.mouse.move(x+44,y,steps=5)
        page.mouse.up()
        after=int(handle.get_attribute('aria-valuenow'))
        assert start+32<=after<=start+50,(start,after)
        assert page.evaluate("""() => window.__documentBefore.isConnected &&
          window.__documentBefore===document.querySelector('[data-mcel-document-editor]')""")
        assert not failures, failures
    finally:
        page.close()


def test_resizing_and_persistence_survive_remount(browser):
    page = browser.new_page(viewport={"width": 1380, "height": 820})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    source = render_semantic_page('code-editor', ROOT, promoted=True)
    # The CI browser blocks all navigation, including localhost. Replace only
    # the storage boundary with an in-memory same-window implementation, then
    # remount the complete page as a reload-equivalent test of persistence.
    page.evaluate("""() => {
      const entries=Object.create(null);
      Object.defineProperty(window,'localStorage',{configurable:true,value:{
        getItem:key=>Object.hasOwn(entries,key)?entries[key]:null,
        setItem:(key,value)=>{entries[key]=String(value);},
        removeItem:key=>{delete entries[key];}
      }});
    }""")
    page.set_content(source)
    try:
        explorer = page.locator('[data-mcel-document-splitter="explorer"]')
        bottom = page.locator('[data-mcel-document-splitter="bottom"]')
        before = int(explorer.get_attribute('aria-valuenow'))
        explorer.focus()
        explorer.press('ArrowRight')
        after = int(explorer.get_attribute('aria-valuenow'))
        assert after == before + 16
        bottom_before = int(bottom.get_attribute('aria-valuenow'))
        bottom.focus()
        bottom.press('ArrowUp')
        bottom_after = int(bottom.get_attribute('aria-valuenow'))
        assert bottom_after == bottom_before + 16
        page.locator('[data-mcel-document-activity="companion"]').nth(1).click()
        assert page.evaluate("""() => Object.keys(JSON.parse(localStorage.getItem('mcel.semantic-document-workbench.v1:code-editor')).preferences).length >= 8""")
        page.evaluate("""() => window.__mcelWorkbench.app.unmount()""")
        page.set_content(source)
        assert page.locator('[data-mcel-document-splitter="explorer"]').get_attribute('aria-valuenow') == str(after)
        assert page.locator('[data-mcel-document-splitter="bottom"]').get_attribute('aria-valuenow') == str(bottom_after)
        assert page.locator('[data-mcel-document-tablist="companion"] [role="tab"]').nth(1).get_attribute('aria-selected') == 'true'
        page.locator('[data-mcel-document-toggle="reset"]').click()
        assert page.locator('[data-mcel-document-splitter="explorer"]').get_attribute('aria-valuenow') == str(before)
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize('width', [390, 760, 1024, 1380])
def test_responsive_docks_retain_editor_and_do_not_overflow(browser, width):
    page, failures = page_for(browser, width)
    try:
        page.locator('[data-mcel-document-activity="companion"]').nth(1).click()
        observed = page.evaluate("""() => {
          const s=document.querySelector('[data-mcel-document-workbench]');
          const editor=s.querySelector('[data-mcel-document-surface]').getBoundingClientRect();
          const companion=s.querySelector('[data-mcel-document-companion]').getBoundingClientRect();
          return {rung:s.dataset.mcelLayoutRung,scroll:document.documentElement.scrollWidth,
            width:innerWidth,editor:editor.width,companionRight:companion.right};
        }""")
        assert observed['scroll'] <= observed['width'], observed
        assert observed['editor'] > 180, observed
        assert observed['companionRight'] <= observed['width'] + 1, observed
        assert observed['rung'] == ('wide' if width >= 1180 else 'medium' if width >= 780 else 'narrow')
        assert not failures, failures
    finally:
        page.close()
