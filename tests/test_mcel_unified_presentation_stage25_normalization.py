"""Patch 2.5: one execution owner, pre-activation parity, and no app exceptions."""
from pathlib import Path
import shutil

import pytest

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from main_computer.mcel_chat_console_host import render_chat_console_page

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
CONTROLLER = (SCRIPTS / "mcel-unified-presentation-controller.js").read_text(encoding="utf8")


@pytest.mark.parametrize("name,render", [
    ("calculator", lambda: render_expanded_page(promoted=True)),
    ("file-explorer", lambda: render_semantic_page("file-manager", promoted=True)),
    ("task-manager", lambda: render_semantic_page("task-manager", promoted=True)),
    ("code-editor", lambda: render_semantic_page("code-editor", promoted=True)),
    ("chat-console", lambda: render_chat_console_page(promoted=True)),
])
def test_promoted_pages_have_exactly_one_controller_and_registry_lookup(name, render):
    html = render()
    signature = 'Object.defineProperty(global, "McelUnifiedPresentationController"'
    assert html.count(signature) == 1, name
    assert html.count("McelApplicationPresentationRegistry.connect(") == 1, name
    assert html.count('id="mcel-application-presentation-selection"') == 1, name
    assert html.index(signature) < html.index("McelApplicationPresentationRegistry.connect("), name
    assert "resolved-no-op" in html
    if name != "chat-console":
        assert html.index(signature) < html.index('global.McelSemanticOperationalRuntime = Object.freeze('), name
        assert "McelUnifiedPresentationController" in html
    assert "applyLabChrome(" in html  # controller loaded; not an automatic Chrome call


def test_all_promoted_semantic_apps_have_no_app_specific_presentation_dispatch():
    runtime = (SCRIPTS / "mcel-semantic-operational-runtime.js").read_text(encoding="utf8")
    lab = (SCRIPTS / "mcel-lab.js").read_text(encoding="utf8")
    app = (ROOT / "main_computer/web/applications.html").read_text(encoding="utf8")
    assert "mountOperationalColor(colorInput)" in runtime
    assert "refreshOperationalColor(root)" in runtime
    assert "setOperationalColorTheme(root,theme,palette)" in runtime
    assert "releaseOperationalColor(root)" in runtime
    assert "global.McelSemanticColorPresentation.mount(" not in runtime
    for independent in (
        "window.McelSemanticThemePreview.apply(",
        "window.McelSemanticFormPresentationLaw.apply(",
        "window.McelSemanticChromePresentationLaw.apply(",
    ):
        assert independent not in lab
    for shared in ("applyLabTheme", "applyLabForm", "applyLabChrome"):
        assert f"McelUnifiedPresentationController?.{shared}" in lab
    include = "<!-- @include applications/scripts/mcel-unified-presentation-controller.js -->"
    assert app.count(include) == 1
    assert app.index(include) < app.index("<!-- @include applications/scripts/mcel-lab.js -->")
    import json
    policy = json.loads((ROOT / "tools/fixtures/mcel_semantic_applications_shell/registry.json").read_text())["presentation"]
    assert policy["system"] == {"theme":"theme-machine","form":"form-current","chrome":"chrome-tng-side-rails"}
    assert policy["rollout"]["defaultEnabled"] is False


def test_shared_controller_owns_existing_color_calls_and_lab_preview_without_activation():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which("chromium") or None)
        page = browser.new_page()
        try:
            page.set_content('<main id="root"><button id="existing">Existing</button></main>')
            page.add_script_tag(content="""(() => {
              window.calls=[];
              const record = name => (calls.push(name), name);
              window.McelSemanticColorPresentation={
                validate:()=>({status:'valid',authored:1}),
                mount:()=>({status:'applied',
                  refresh:()=>record('color-refresh'),
                  setTheme:()=>({status:record('color-theme')}),
                  report:()=>({status:'applied'}),
                  unmount:()=>record('color-unmount')})
              };
              window.McelSemanticThemePreview={apply:()=>({status:record('lab-theme')})};
              window.McelSemanticFormPresentationLaw={apply:()=>({status:record('lab-form')})};
              window.McelSemanticChromePresentationLaw={apply:()=>({status:record('lab-chrome')})};
            })()""")
            page.add_script_tag(content=CONTROLLER)
            result = page.evaluate("""() => {
              const root=document.getElementById('root');
              const profiles={theme:'theme-machine',form:'form-current',chrome:'chrome-tng-side-rails'};
              const ctrl=McelUnifiedPresentationController;
              const baseline=root.outerHTML;
              const plan=ctrl.prepare({root,appId:'calculator',profiles,rolloutEnabled:true});
              const prepared=[plan.status,root.outerHTML===baseline,calls.length];
              const validation=ctrl.validateOperationalColor({});
              const one=ctrl.mountOperationalColor({root});
              const two=ctrl.mountOperationalColor({root});
              ctrl.refreshOperationalColor(root);
              const report=ctrl.operationalColorReport(root);
              const set=ctrl.setOperationalColorTheme(root,'theme-machine',{});
              const beforeLab=calls.slice();
              const request={root,appId:'calculator',profiles,ir:{},bridge:{},
                theme:'theme-machine',profile:'chrome-tng-side-rails'};
              const surfaceConflict=ctrl.prepare({root,appId:'calculator',profiles,surface:'lab'});
              const labRoot=document.createElement('section');
              const labRequest={...request,root:labRoot};
              const lab=[ctrl.applyLabTheme(labRequest).status,ctrl.applyLabForm(labRequest).status,
                ctrl.applyLabChrome(labRequest).status];
              ctrl.release(labRoot);
              ctrl.release(root);
              return {prepared,validation:validation.status,one:one===two,
                report:report.status,set:set.status,beforeLab,lab,allCalls:calls,
                remaining:ctrl.inspect(root),surfaceConflict:surfaceConflict.reason,
                unchanged:root.outerHTML===baseline};
            }""")
            assert result["prepared"] == ["prepared-no-op", True, 0]
            assert result["validation"] == "valid" and result["report"] == "applied"
            assert result["one"]
            assert result["beforeLab"] == ["color-refresh", "color-theme"]
            assert result["lab"] == ["lab-theme", "lab-form", "lab-chrome"]
            assert result["allCalls"] == ["color-refresh", "color-theme", "lab-theme",
                                           "lab-form", "lab-chrome", "color-unmount"]
            assert result["surfaceConflict"] == "root-already-owned-by-other-surface"
            assert result["remaining"] is None and result["unchanged"]
        finally:
            browser.close()
