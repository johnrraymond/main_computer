"""System-wide MCEL presentation defaults: real mounted-app/browser acceptance.

Presentation is owned by the common MCEL runtime. Apps author semantics, not
color/shape/material instructions. The same registry policy applies to SLH and
all five other promoted semantic apps, with the launcher inventory unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

import pytest

from main_computer.mcel_applications_registry import read_registry, resolve_presentation
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_chat_console_host import render_chat_console_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_slh_application import render_slh_page

ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {"theme": "theme-machine", "form": "form-current", "chrome": "chrome-tng-side-rails"}


@pytest.mark.parametrize("app", ["calculator", "file-explorer", "task-manager", "code-editor", "chat-console", "slh"])
def test_system_selection_inherits_for_all_semantic_mounts(app):
    registry = read_registry(ROOT)
    assert len(registry["apps"]) == 22  # SLH is a first-class launcher app
    selection = resolve_presentation(registry, app)
    assert selection["profiles"] == EXPECTED
    assert selection["eligible"] and selection["rolloutEnabled"]
    assert not resolve_presentation(registry, app, promoted=False)["rolloutEnabled"]


def test_registry_override_and_nonlauncher_slh_disable_are_independent():
    registry = deepcopy(read_registry(ROOT))
    editor = next(x for x in registry["apps"] if x["id"] == "code-editor")
    editor["presentation"] = {"chrome": "chrome-strict-hierarchy"}
    assert resolve_presentation(registry, "code-editor")["profiles"] == {
        **EXPECTED, "chrome": "chrome-strict-hierarchy"
    }
    registry["presentation"]["rollout"]["apps"]["slh"] = False
    assert not resolve_presentation(registry, "slh")["rolloutEnabled"]
    assert resolve_presentation(registry, "calculator")["rolloutEnabled"]
    assert not resolve_presentation(registry, "desktop")["rolloutEnabled"]


RENDER = {
    "calculator": lambda: render_expanded_page(ROOT, promoted=True),
    "file-explorer": lambda: render_semantic_page("file-manager", ROOT, promoted=True),
    "task-manager": lambda: render_semantic_page("task-manager", ROOT, promoted=True),
    "code-editor": lambda: render_semantic_page("code-editor", ROOT, promoted=True),
    "chat-console": lambda: render_chat_console_page(ROOT, promoted=True),
    "slh": lambda: render_slh_page("overview", ROOT),
}


@pytest.mark.parametrize("app", list(RENDER))
def test_real_browser_mounts_apply_all_three_laws_without_errors(app):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.evaluate("""() => {for(const name of ['localStorage','sessionStorage']){
          const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
            getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value)),
            removeItem:key=>values.delete(key),clear:()=>values.clear()}});}}""")
        try:
            page.set_content(RENDER[app](), wait_until="domcontentloaded", timeout=12000)
            state = page.evaluate("""() => {
              const root=document.getElementById(window.__mcelSystemPresentationSelection?.appId==='calculator'
                ? 'mcel-expanded-root' : window.__mcelSystemPresentationSelection?.appId==='chat-console'
                ? 'chat-console-app' : 'mcel-workbench');
              const report=window.__mcelPresentationReport;
              return {status:report?.status,steps:report?.steps,profiles:report?.profiles,
                law:!!window.McelSemanticColorPresentation,
                controller:!!window.McelUnifiedPresentationController,
                mounted:!!root?.children.length,semanticColor:!!root?.hasAttribute('data-mcel-semantic-color-full'),
                shape:!!root?.hasAttribute('data-mcel-form-presentation-root'),
                materials:root?.querySelectorAll('[data-mcel-chrome-material]').length||0,
                rails:root?.querySelectorAll('[data-mcel-chrome-generated="true"]').length||0,
                error:document.querySelector('#mcel-expanded-diagnostic')?.getAttribute('data-error')==='true'
                  ?document.querySelector('#mcel-expanded-diagnostic').textContent :null};
            }""")
            assert errors == [], (app,errors)
            assert state["status"] == "applied", (app,state)
            assert state["steps"] == {"theme":"applied", "form":"applied", "chrome":"applied"}, (app,state)
            assert state["profiles"] == EXPECTED and state["mounted"], (app,state)
            assert state["law"] and state["controller"] and state["semanticColor"] and state["shape"],(app,state)
            assert state["materials"] > 0, (app,state)
            assert state["rails"] in (0,2), (app,state)
            assert state["error"] is None, (app,state)
        finally:
            browser.close()


def test_chrome_reloaded_and_lab_reapplied_do_not_duplicate_calculator_rails():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page(viewport={"width":1440,"height":850})
        page.evaluate("""() => {for(const name of ['localStorage','sessionStorage']){
          const values=new Map();Object.defineProperty(window,name,{configurable:true,value:{
            getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value)),
            removeItem:key=>values.delete(key),clear:()=>values.clear()}});}}""")
        try:
            page.set_content(render_expanded_page(ROOT,promoted=True))
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            page.add_script_tag(path=str(ROOT / 'main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js'))
            state=page.evaluate("""() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const rails=()=>root.querySelectorAll('[data-mcel-chrome-generated="true"]').length;
              const first=rails();
              const apply=(profile)=>McelSemanticChromePresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,profile,theme:'theme-machine',styleLaw:McelLabStyleLaw});
              const second=apply('chrome-tng-side-rails');const after=rails();
              const removed=apply('chrome-strict-hierarchy');const empty=rails();
              const third=apply('chrome-tng-side-rails');const final=rails();
              return {first,second:second.status,after,removed:removed.status,empty,
                third:third.status,final,buttons:root.querySelectorAll('button').length};
            }""")
            assert state['first']==state['after']==state['final']==2,state
            assert state['empty']==0 and state['buttons']>0,state
            assert state['second']==state['third']=='applied' and state['removed']=='unchanged',state
        finally:
            browser.close()


def test_missing_color_dependency_does_not_crash_slh_operational_mount():
    """A broken presentation asset degrades appearance, never SLH operation."""
    from playwright.sync_api import sync_playwright
    source = (ROOT / "main_computer/web/applications/scripts/mcel-semantic-color-presentation.js").read_text(encoding="utf-8")
    html = render_slh_page("overview", ROOT)
    script = "<script>\n" + source + "\n</script>\n"
    assert html.count(script) == 1
    html = html.replace(script, "", 1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content(html, wait_until="domcontentloaded", timeout=12000)
            outcome = page.evaluate("""() => ({
                mounted:document.getElementById('mcel-workbench')?.hasAttribute('data-mcel-semantic-operational'),
                controls:document.querySelectorAll('#mcel-workbench button').length,
                report:window.__mcelPresentationReport?.status,
                diagnostic:window.__mcelPresentationDiagnostic?.reason,
                status:document.getElementById('mcel-workbench-status')?.textContent
            })""")
            assert errors == [], errors
            assert outcome["mounted"] and outcome["controls"] > 0, outcome
            assert outcome["report"] == "partial", outcome
            assert outcome["diagnostic"] == "semantic-color-inference-engine-missing", outcome
            assert "mount refused" not in (outcome["status"] or "").lower(), outcome
        finally:
            browser.close()


def test_corrupt_presentation_registry_cannot_block_slh_host(monkeypatch):
    """Registry failure is isolated from the semantic SLH app host."""
    import main_computer.mcel_applications_registry as registry_module
    def unavailable(_):
        raise ValueError("corrupt presentation registry")
    monkeypatch.setattr(registry_module, "read_registry", unavailable)
    html = render_slh_page("overview", ROOT)
    assert '"rolloutEnabled":false' in html
    assert '"status":"registry-unavailable"' in html
    assert 'McelSemanticOperationalRuntime.mount' in html


def test_form_current_preserves_geometry_in_standalone_calculator():
    """System default Form must not negotiate geometry from legacy layout."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.evaluate("""() => {for(const name of ['localStorage','sessionStorage']){
          const m=new Map();Object.defineProperty(window,name,{configurable:true,value:{
          getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,String(v))}})}}""")
        try:
            page.set_content(render_expanded_page(ROOT,promoted=True))
            outcome = page.evaluate("""() => {
              const root=document.getElementById('mcel-expanded-root');
              const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
              const before=[...root.querySelectorAll('button,input')].map(e=>{
                const r=e.getBoundingClientRect();return [r.left,r.top,r.width,r.height];});
              const applied=McelSemanticFormPresentationLaw.apply({root,ir,
                bridge:McelSemanticPresentationBridge,profile:'form-current'});
              const after=[...root.querySelectorAll('button,input')].map(e=>{
                const r=e.getBoundingClientRect();return [r.left,r.top,r.width,r.height];});
              return {status:applied.status,layout:applied.shapeLayout,before,after};
            }""")
            assert outcome["status"] == "applied-in-isolated-lab-frame", outcome
            assert outcome["layout"]["status"] == "preserved" and outcome["layout"]["adjusted"] == 0
            assert outcome["before"] == outcome["after"], outcome
        finally:
            browser.close()


@pytest.mark.parametrize("view", ["worker-bank", "network", "remote-seh"])
def test_all_slh_destination_views_mount_with_system_presentation(view):
    """SLH route variation must not require app-specific presentation assets."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content(render_slh_page(view, ROOT), wait_until="domcontentloaded", timeout=12000)
            result = page.evaluate("""() => ({
                status:window.__mcelPresentationReport?.status,
                steps:window.__mcelPresentationReport?.steps,
                root:document.getElementById('mcel-workbench')?.getAttribute('data-mcel-semantic-operational'),
                links:[...document.querySelectorAll('a[href^="/applications/slh"]')].length,
                message:document.getElementById('mcel-workbench-status')?.textContent
            })""")
            assert errors == [] and result["status"] == "applied", (view, errors, result)
            assert result["steps"] == {"theme":"applied","form":"applied","chrome":"applied"}, (view,result)
            assert result["root"] == "slh" and result["links"] > 0, (view,result)
            assert "mount refused" not in (result["message"] or "").lower(), (view,result)
        finally:
            browser.close()


def test_missing_unified_controller_does_not_crash_slh_operation():
    """The original undefined===undefined controller bug stays fixed."""
    from playwright.sync_api import sync_playwright
    source = (ROOT / "main_computer/web/applications/scripts/mcel-unified-presentation-controller.js").read_text(encoding="utf-8")
    html = render_slh_page("overview", ROOT)
    script = "<script>\n" + source + "\n</script>\n"
    assert html.count(script) == 1
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.set_content(html.replace(script, "", 1), wait_until="domcontentloaded", timeout=12000)
            outcome = page.evaluate("""() => ({
                mounted:document.getElementById('mcel-workbench')?.getAttribute('data-mcel-semantic-operational'),
                controller:!!window.McelUnifiedPresentationController,
                actions:document.querySelectorAll('#mcel-workbench button').length,
                status:document.getElementById('mcel-workbench-status')?.textContent
            })""")
            assert errors == [], errors
            assert outcome["mounted"] == "slh" and not outcome["controller"], outcome
            assert outcome["actions"] > 0 and "mount refused" not in (outcome["status"] or "").lower(),outcome
        finally:
            browser.close()
