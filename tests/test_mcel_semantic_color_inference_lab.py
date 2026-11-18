"""MCEL semantic Color Law inference: canonical meaning, no app color authoring.

Checks the same iframe mount used by the Lab, with all three existing
presentation laws coordinated through the Stage 2.5 unified controller.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
STORAGE_SHIM = """(() => { for (const name of ['localStorage','sessionStorage']) {
  const values = new Map(); Object.defineProperty(window,name,{configurable:true,value:{
    getItem:key=>values.get(key)||null, setItem:(key,value)=>values.set(key,String(value)),
    removeItem:key=>values.delete(key),clear:()=>values.clear()
  }});
}})();"""
LAB_SCRIPTS = (
    "mcel-contract.js", "mcel-law-registry.js", "mcel-style-law.js",
    "mcel-chrome-law.js", "mcel-form-law.js", "mcel-semantic-layout-inference.js",
    "mcel-semantic-presentation-bridge.js", "mcel-semantic-theme-preview.js",
    "mcel-semantic-form-presentation-law.js", "mcel-semantic-chrome-presentation-law.js",
    "mcel-unified-presentation-controller.js",
)


def test_calculator_semantic_ir_requires_no_authored_color_contract():
    ir = compile_expanded(ROOT)
    surface = ir["surfaces"][0]
    assert len(surface["nodes"]) == 14
    assert len(surface["interfaceIntent"]["regions"]) == 13
    assert all("color" not in record for record in
               [*surface["nodes"], *surface["interfaceIntent"]["regions"]])
    assert "data-mcel-color-" not in (ROOT / "tools/fixtures/mcel_calculator_expanded/application.html").read_text()
    runtime = (SCRIPTS / "mcel-semantic-operational-runtime.js").read_text()
    law = (SCRIPTS / "mcel-semantic-color-presentation.js").read_text()
    assert "n.color?.role" not in runtime
    assert "function inferRecords(ir)" in law
    assert "authored-color-contract-forbidden" in law
    assert "data-mcel-generated-color-role" in law  # Legacy-only branch remains isolated.


@pytest.mark.parametrize("app", ["calculator", "file-manager", "task-manager", "code-editor"])
def test_lab_mount_uses_real_inferred_color_form_and_chrome(app):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_init_script(STORAGE_SHIM)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.set_content('<!doctype html><html><body><iframe id="app" style="width:1100px;height:700px"></iframe></body></html>')
            for filename in LAB_SCRIPTS:
                page.add_script_tag(path=str(SCRIPTS / filename))
            html = render_expanded_page(ROOT, promoted=True) if app == "calculator" else \
                render_semantic_page(app, ROOT, promoted=True)
            # srcdoc has no URL query string; emulate the actual Lab iframe flag.
            html = html.replace('</head>', '<script>window.__mcelSemanticColorLabPreview=true;</script></head>')
            page.evaluate('(html) => document.getElementById("app").srcdoc=html', html)
            page.wait_for_function("""() => !!document.querySelector('iframe')
              .contentDocument?.querySelector('[data-mcel-semantic-color-full]')""", timeout=10000)
            result = page.evaluate("""() => {
              const frame=document.getElementById('app'), doc=frame.contentDocument,
                win=frame.contentWindow, root=doc.querySelector('[data-mcel-semantic-color-full]');
              const handle=win.__mcelExpanded || win.__mcelWorkbench;
              const ir=JSON.parse(doc.getElementById(win.__mcelExpanded?
                'mcel-expanded-ir':'mcel-workbench-ir').textContent);
              const reports=[];
              const button=root.querySelector('button[type="submit"]')||root.querySelector('button');
              const original=button;
              let before=null;
              for(const theme of ['theme-machine','theme-civic']) {
                const profiles={theme,form:'form-current',chrome:'chrome-tng-side-rails'};
                const request={root,ir,appId:ir.application.appId,profiles,bindingModel:'operational-runtime',
                  bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,theme,
                  tokens:McelLabStyleLaw.themePaletteFor(theme)};
                const t=McelUnifiedPresentationController.applyLabTheme(request);
                const f=McelUnifiedPresentationController.applyLabForm({...request,profile:'form-current'});
                const c=McelUnifiedPresentationController.applyLabChrome({...request,profile:'chrome-tng-side-rails'});
                const report=handle.app.colorPresentation();
                if(!before)before=button.getBoundingClientRect();
                reports.push({theme,themeStatus:t.status,formStatus:f.status,chromeStatus:c.status,
                  lawSource:report.source,authored:report.authored,inferred:report.inferred,
                  regions:t.regions,controls:t.controls,
                  sample:doc.defaultView.getComputedStyle(button).backgroundColor});
              }
              const after=button?.getBoundingClientRect();
              return {reports,errors:[],unmounted:false,
                generated:root.querySelectorAll('[data-mcel-generated-color-role]').length,
                colored:root.querySelectorAll('[data-mcel-color-state]').length,
                styleCount:doc.querySelectorAll('style[data-mcel-semantic-color-law]').length,
                sameControl:button===original,
                layoutStable:!!before&&!!after&&before.x===after.x&&before.y===after.y&&
                  before.width===after.width&&before.height===after.height};
            }""")
            assert errors == [], (app, errors)
            assert result["generated"] == 0 and result["styleCount"] == 1, (app, result)
            assert result["colored"] > 0 and result["sameControl"] and result["layoutStable"], (app, result)
            assert len({r["sample"] for r in result["reports"]}) == 2, (app, result)
            for report in result["reports"]:
                assert report["themeStatus"] == "applied-in-isolated-lab-frame", (app, report)
                assert report["formStatus"] == "applied-in-isolated-lab-frame", (app, report)
                assert report["chromeStatus"] in {"applied", "unchanged"}, (app, report)
                assert report["lawSource"] == "semantic-inference", (app, report)
                assert report["authored"] == 0 and report["inferred"] > 0, (app, report)
                assert report["regions"] > 0 and report["controls"] > 0, (app, report)
        finally:
            context.close()
            browser.close()
