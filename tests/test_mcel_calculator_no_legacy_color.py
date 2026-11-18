"""Calculator geometry is application-owned; all physical colors belong to MCEL laws."""
from pathlib import Path

import re
from playwright.sync_api import sync_playwright

from main_computer.mcel_applications_registry import read_registry, resolve_presentation
from main_computer.mcel_calculator_expanded_host import render_expanded_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
STYLES = [ROOT / "main_computer/web/applications/styles/mcel-semantic-workbench.css",
          ROOT / "main_computer/web/applications/styles/mcel-calculator-graph-inspection.css"]
PAINT_PROPERTIES = {
    "color", "color-scheme", "background", "background-color", "background-image",
    "border-color", "outline-color", "fill", "stroke", "caret-color",
    "accent-color", "box-shadow", "text-shadow", "text-decoration-color",
    "-webkit-text-fill-color",
}


def test_calculator_style_has_geometry_but_no_application_owned_colors():
    # Workbench also serves other applications. Only the Calculator-scoped
    # section is subject to the Calculator's no-authored-color contract.
    shared = STYLES[0].read_text(encoding="utf8")
    calculator = shared.split('/* Calculator semantic background rules (formerly expert.css). */',1)[1]
    css = calculator + "\n" + STYLES[1].read_text(encoding="utf8")
    executable = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for property_name in PAINT_PROPERTIES:
        assert not re.search(r"(?:^|[;{])\s*" + re.escape(property_name) +
                             r"\s*:", executable, flags=re.M), property_name
    assert not re.search(r"(?:^|[;{])\s*--mcel-", executable, flags=re.M)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", executable)
    assert "grid-template-columns" in executable
    assert "border-radius" in executable


def test_calculator_standalone_rollout_disabled_like_all_other_apps():
    registry = read_registry(ROOT)
    assert registry["presentation"]["rollout"] == {"defaultEnabled": False, "apps": {}}
    for app_id in ("calculator", "file-explorer", "task-manager", "code-editor", "chat-console"):
        assert resolve_presentation(registry, app_id)["rolloutEnabled"] is False


def test_deployed_calculator_has_no_legacy_color_and_lab_uses_same_inference():
    with sync_playwright() as p:
        browser = _chrome(p, None)
        context = browser.new_context(viewport={"width": 1100, "height": 850})
        context.add_init_script("""for (const name of ['localStorage','sessionStorage']) {
            const m=new Map();Object.defineProperty(window,name,{configurable:true,value:{
            getItem:k=>m.get(String(k))||null,setItem:(k,v)=>m.set(String(k),String(v)),
            removeItem:k=>m.delete(String(k)),clear:()=>m.clear()}})}""")
        try:
            page = context.new_page()
            baseline = render_expanded_page(ROOT, promoted=True)
            page.set_content(baseline)
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            before=page.evaluate("""() => ({
                mounted:!!window.__mcelExpanded,
                oldRole:document.querySelectorAll('[data-mcel-color-state]').length,
                oldLaw:document.querySelectorAll('style[data-mcel-semantic-color-law]').length,
                hasPad:document.querySelectorAll('[data-mcel-expression-pad] button').length,
                rollout:window.__mcelSemanticColorRollout
            })""")
            assert before == {"mounted": True,"oldRole":0,"oldLaw":0,"hasPad":21,"rollout":False},before
            page.locator('[data-mcel-node$="arithmetic-expression"] input').fill('2+3')
            page.locator('[data-mcel-node$="arithmetic-expression"] button[type="submit"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-node$=calculated-value]')?.textContent?.trim()==='5'")
            page.close()
            # Lab-preview activates the same canonical Color Law through the existing registry/controller.
            page=context.new_page()
            lab=baseline.replace('</head>',
                '<script>window.__mcelSemanticColorLabPreview=true;</script></head>')
            page.set_content(lab)
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            law=page.evaluate("""() => ({
                role:document.querySelectorAll('[data-mcel-color-state]').length,
                color:window.__mcelExpanded.app.colorPresentation(),
                styles:document.querySelectorAll('style[data-mcel-semantic-color-law]').length
            })""")
            assert law['role']==111 and law['styles']==1,law
            assert law['color']['source']=='semantic-inference' and law['color']['authored']==0,law
            page.close()
        finally:
            context.close()
            browser.close()
