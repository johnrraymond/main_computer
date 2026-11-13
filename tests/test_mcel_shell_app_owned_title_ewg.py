"""Native app identity and E/W/G must survive canonical shell mounting.

The test mounts the *real* shell/child browser code, with browser-local
clipboard capture and no service or network mutations.  It deliberately
avoids forwarding diagnostic content across the parent iframe boundary.
"""
from __future__ import annotations

import json
import shutil

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_applications_shell_host import (
    compile_applications_shell, render_applications_shell_candidate,
)
from main_computer.mcel_calculator_expanded_host import render_expanded_page


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        b = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None,
                                        args=["--no-sandbox"])
        try:
            yield b
        finally:
            b.close()


def test_compiled_parent_shell_has_no_application_title_region():
    ir, _ = compile_applications_shell()
    region_ids = {r["id"].split(".")[-1] for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    assert region_ids == {"application", "masthead", "shell", "launcher", "workspace", "application-mount", "status"}
    assert "workspace-header" not in region_ids
    assert not any(n.get("id") == "workspace-header" for n in
                   ir["layouts"][0]["layoutTree"]["root"].get("children", []))
    markup = render_applications_shell_candidate(canonical_promoted=True)
    assert 'id="mcel-shell-active-title"' not in markup
    assert 'class="mcel-shell-workspace-header"' not in markup


@pytest.mark.parametrize("width", [390, 1040])
def test_shell_mount_preserves_child_title_and_copies_ewg_from_child(browser, width):
    page = browser.new_page(viewport={"width": width, "height": 830})
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        page.set_content(render_applications_shell_candidate(canonical_promoted=True))
        assert page.locator("body").get_attribute("data-mcel-shell-ready") == "true"
        assert page.locator('#mcel-shell-active-title,[data-mcel-application-title]').count() == 0
        assert page.evaluate('window.__mcelApplicationsShell.select("calculator",false)')
        assert page.locator('body').get_attribute('data-mcel-shell-active-app') == 'calculator'
        frame = page.frames[-1]
        assert frame.name == 'mcel-shell-frame'
        frame.evaluate('''() => {
          const data = new Map();
          const memory = {getItem:k=>data.get(String(k))??null,
            setItem:(k,v)=>data.set(String(k),String(v)),
            removeItem:k=>data.delete(String(k)),clear:()=>data.clear()};
          Object.defineProperty(window,'localStorage',{configurable:true,value:memory});
          window.__copied=[];
          Object.defineProperty(navigator,'clipboard',{configurable:true,
            value:{writeText:async text=>window.__copied.push(text)}});
        }''')
        frame.set_content(render_expanded_page(promoted=True))
        assert frame.locator('[data-mcel-application-title]').inner_text() == 'Semantic Calculator'
        ewg = frame.locator('[data-mcel-diagnostic-title-control]')
        assert ewg.count() == 1
        assert ewg.evaluate('el=>el.previousElementSibling===document.querySelector("[data-mcel-application-title]")')
        ewg.click()
        frame.wait_for_function('window.__copied.length===1')
        report = json.loads(frame.evaluate('window.__copied[0]'))
        assert report['schema'] == 'mcel-diagnostics-counter-copy-v4'
        assert report['appId'] == 'semantic-calculator-expanded'
        assert report['diagnosticEvidence']['schema'] == 'mcel-diagnostic-evidence-v1'
        assert frame.locator('dialog:visible,[role="dialog"]:visible').count() == 0
        assert page.locator('body').get_attribute('data-mcel-shell-ready') == 'true'
        assert page.locator('#mcel-shell-active-title,[data-mcel-application-title]').count() == 0
        assert errors == [], errors
    finally:
        page.close()
