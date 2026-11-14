"""MCEL Lab preview height regression: preserve the original framed Calculator viewport.

The Lab's 56dvh override reduced a previously 65vh preview and generated
1px native-scrollbar overflow in the real browser at 0.75 device scale.
Only Lab preview sizing is in scope; semantic Calculator layout is untouched.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "main_computer/web/applications/styles/mcel-lab.css"


def test_calculator_preview_height_has_one_canonical_rule():
    css = CSS.read_text(encoding="utf-8")
    rules = re.findall(r"#mcel-lab-app\s+#mcel-semantic-theme-frame\s*\{([^{}]*)\}", css)
    assert len(rules) == 2, "Only the original frame rule and appearance-only extension are expected"
    assert re.findall(r"\bheight\s*:\s*([^;]+);", rules[0]) == ["clamp(350px, 65vh, 750px)"]
    assert not re.search(r"(?<![\w-])height\s*:", rules[1]), "Appearance extension must not shrink the preview"
    assert "56dvh" not in "".join(rules)


def test_original_height_preserves_calculator_and_lab_scrolling_in_browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    # Use the promoted Calculator page in a real same-origin sandboxed frame,
    # not a hand-built clone. Navigation and workspace remain native scrollers.
    calculator = render_expanded_page(ROOT, promoted=True)
    with sync_playwright() as p:
        browser = _chrome(p, None)
        context = browser.new_context(viewport={"width": 975, "height": 690}, device_scale_factor=0.75)
        page = context.new_page()
        page.set_content('''<!doctype html><html><head><style>
          body{margin:0;background:#080812;color:white}
          #mcel-lab-app{width:920px;height:500px;overflow-y:auto}
          .mcel-semantic-theme-experiment{max-width:900px}
          .mcel-lab-test-following-content{height:420px}
        </style></head><body><div id="mcel-lab-app">
          <section class="mcel-semantic-theme-experiment">
            <iframe id="mcel-semantic-theme-frame" sandbox="allow-scripts allow-same-origin"></iframe>
          </section><section class="mcel-lab-test-following-content">Lab diagnostics</section>
        </div></body></html>''')
        page.add_style_tag(path=str(CSS))
        page.locator("#mcel-semantic-theme-frame").evaluate("(el, html) => el.srcdoc=html", calculator)
        page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-frame')
          ?.contentDocument?.querySelector('#mcel-expanded-root [data-mcel-natural-scroller="navigation"]')''', timeout=20000)
        page.evaluate('''() => {
          const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
          const basic=[...doc.querySelectorAll('[data-mcel-mode-tabs] button')]
            .find(el=>el.textContent.trim()==='Basic');
          if(!basic) throw Error('real semantic Basic mode control missing');
          basic.click();
        }''')
        page.wait_for_timeout(100)

        measure = '''() => {
          const frame=document.getElementById('mcel-semantic-theme-frame');
          const doc=frame.contentDocument;
          const scrollers=[...doc.querySelectorAll('#mcel-expanded-root [data-mcel-natural-scroller]')];
          const lab=document.getElementById('mcel-lab-app');
          return {height:frame.getBoundingClientRect().height,
            viewport:frame.contentWindow.innerHeight,
            scrollports:scrollers.map(el=>({role:el.getAttribute('data-mcel-natural-scroller'),
              client:el.clientHeight,scroll:el.scrollHeight,travel:el.scrollHeight-el.clientHeight,
              overflow:getComputedStyle(el).overflowY,scroller:el})).map(({scroller,...x})=>x),
            labTravel:lab.scrollHeight-lab.clientHeight};
        }'''
        restored = page.evaluate(measure)
        assert 448 <= restored["height"] <= 452, restored
        assert {r["role"] for r in restored["scrollports"]} == {"navigation", "workspace", "inspection"}
        assert all(r["travel"] <= 0 for r in restored["scrollports"]), restored
        assert restored["labTravel"] > 200, restored

        # Exercise a genuinely long canonical Calculator mode in the short
        # preview; the semantic workspace must remain natively scrollable.
        page.locator("#mcel-semantic-theme-frame").evaluate("el => el.style.height='250px'")
        page.evaluate("""() => {
          const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
          const graphing=[...doc.querySelectorAll('[data-mcel-mode-tabs] button')]
            .find(el=>el.textContent.trim()==='Graphing');
          if(!graphing) throw Error('real Graphing mode missing');
          graphing.click();
        }""")
        cramped = page.evaluate(measure)
        assert cramped["height"] < restored["height"] - 100
        overflowing = [r for r in cramped["scrollports"] if r["travel"] > 10]
        assert overflowing, cramped
        assert all(r["overflow"] == "auto" for r in overflowing), cramped
        can_scroll = page.evaluate('''() => {
          const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
          const els=[...doc.querySelectorAll('#mcel-expanded-root [data-mcel-natural-scroller]')];
          return els.filter(el=>el.scrollHeight-el.clientHeight>10).map(el=>{
            const old=el.scrollTop;
            el.scrollTop=old+15;
            const moved=el.scrollTop>old+1;
            el.scrollTop=old;
            return moved;
          });
        }''')
        assert can_scroll and all(can_scroll), can_scroll
        page.evaluate("""() => {
          const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
          const basic=[...doc.querySelectorAll('[data-mcel-mode-tabs] button')]
            .find(el=>el.textContent.trim()==='Basic');
          basic.click();
        }""")
        page.locator("#mcel-semantic-theme-frame").evaluate("el => el.style.removeProperty('height')")
        after = page.evaluate(measure)
        assert abs(restored["height"] - after["height"]) <= 0.5
        assert all(r["travel"] <= 0 for r in after["scrollports"]), after
        assert after["labTravel"] > 200
        context.close()
        browser.close()
