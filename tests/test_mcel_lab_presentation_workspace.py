"""MCEL Lab presentation workspace and scroll-owner browser regression."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "main_computer/web/applications"
HTML = APPS / "apps/mcel-lab.html"
CSS = APPS / "styles/mcel-lab.css"
APP_LAYOUT = APPS / "styles/app-layout.css"
LIFECYCLE = APPS / "scripts/mcel-lifecycle-acid-lab.js"
RUNTIME = APPS / "scripts/mcel-lab.js"


def test_presentation_workspace_retains_single_semantic_theme_and_chrome_controls():
    html = HTML.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert html.count('id="mcel-theme-select"') == 1
    assert html.count('id="mcel-chrome-select"') == 1
    assert html.count('id="mcel-presentation-preview-frame"') == 1
    assert 'changeMcelTheme("theme-select")' in runtime
    assert 'changeMcelChrome("chrome-select")' in runtime
    assert 'isolatedSiteDocument(runtimeHtml' in runtime
    assert 'presentationFrame.srcdoc = documentHtml;' in runtime
    assert 'syncMcelRenderedSiteFrame(reason);' in runtime


def test_presentation_and_blueprint_scroll_with_application_not_rail():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    html = HTML.read_text(encoding="utf-8")
    css = APP_LAYOUT.read_text(encoding="utf-8") + "\n" + CSS.read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={"width": width, "height": 720})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_content(
                '<!doctype html><html><body style="margin:0">'
                '<div style="height:580px;width:100%;overflow:hidden">'
                + html + '</div></body></html>'
            )
            page.add_style_tag(content=css)
            page.evaluate("document.getElementById('mcel-lab-app').style.display='grid'")
            page.add_script_tag(path=str(LIFECYCLE))

            def can_scroll_root():
                return page.evaluate("""() => {
                  const lab = document.getElementById('mcel-lab-app');
                  const previous = lab.scrollTop;
                  lab.scrollTop = lab.scrollHeight;
                  const result = {
                    overflow: getComputedStyle(lab).overflowY,
                    moves: lab.scrollTop > 0,
                    height: lab.clientHeight,
                    contentHeight: lab.scrollHeight
                  };
                  lab.scrollTop = previous;
                  return result;
                }""")

            assert page.locator('#mcel-lab-presentation-workspace').is_visible()
            assert not page.locator('.mcel-lab-blueprint-topbar').is_visible()
            presentation = can_scroll_root()
            assert presentation['overflow'] == 'auto'
            assert presentation['moves'], (width, presentation)

            page.locator('#mcel-lab-workspace-blueprint').click()
            assert page.locator('.mcel-lab-blueprint-topbar').is_visible()
            assert not page.locator('#mcel-lab-presentation-workspace').is_visible()
            blueprint = can_scroll_root()
            assert blueprint['moves'], (width, blueprint)
            rail = page.evaluate("""() => {
              const rail = document.querySelector('.mcel-lab-blueprint-right-rail');
              return getComputedStyle(rail).overflowY;
            }""")
            assert rail == 'visible', (width, rail)

            page.locator('#mcel-lab-workspace-lifecycle').click()
            assert page.locator('#mcel-lifecycle-acid').is_visible()
            assert not page.locator('.mcel-lab-blueprint-topbar').is_visible()
            page.locator('#mcel-lab-workspace-presentation').click()
            assert page.locator('#mcel-lab-presentation-workspace').is_visible()
            assert not errors, (width, errors)
            page.close()
        browser.close()
