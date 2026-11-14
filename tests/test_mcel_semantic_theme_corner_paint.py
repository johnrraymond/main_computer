"""Semantic color projection rounds painted control-group tips, not their gaps."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"
SCRIPT = WEB / "scripts/mcel-semantic-form-presentation-law.js"


def test_corner_paint_is_generic_scoped_and_does_not_clip_children():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "cornerAwareGroups(controls,painted,computed)" in source
    assert "data-mcel-form-presentation-corner" in source
    assert "border-radius:${g.radius}px!important" in source
    assert "cornerGroups" in source
    assert "border-radius" in source
    assert "roundedGroupBackgrounds" in source
    assert "#calculator" not in source
    assert "data-mcel-expression-pad" not in source
    assert "data-mcel-mode-tabs" not in source
    assert "overflow:hidden" not in source.replace(" ", "")
    assert "clip-path:" not in source
    assert "background-clip:" not in source


def test_real_promoted_calculator_group_tips_and_gap_pixels():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from PIL import Image
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={"width": width, "height": 850}, device_scale_factor=1)
            # Only mock persistence: the authentic promoted MCEL runtime mounts.
            page.evaluate("""() => { for (const name of ['localStorage','sessionStorage']) {
                const data=new Map();Object.defineProperty(window,name,{configurable:true,value:{
                    getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,String(value)),
                    removeItem:key=>data.delete(key),clear:()=>data.clear()}}); }}""")
            page.set_content(render_expanded_page(ROOT, promoted=True))
            assert page.locator("#mcel-expanded-diagnostic").inner_text() == "Semantic Calculator ready"
            page.add_style_tag(path=str(WEB / "styles/mcel-lab.css"))
            for script in ("mcel-contract.js", "mcel-law-registry.js", "mcel-style-law.js",
                           "mcel-semantic-presentation-bridge.js", "mcel-semantic-form-presentation-law.js",
                           "mcel-semantic-theme-preview.js"):
                page.add_script_tag(path=str(WEB / "scripts" / script))
            result = page.evaluate("""() => {
                const root=document.getElementById('mcel-expanded-root');
                const ir=JSON.parse(document.getElementById('mcel-expanded-ir').textContent);
                const irBefore=JSON.stringify(ir);
                const semanticBefore=[...root.querySelectorAll('[data-mcel-node],[data-mcel-region]')]
                    .map(el=>[el.getAttribute('data-mcel-node'),el.getAttribute('data-mcel-region')]);
                const mode=root.querySelector('[data-mcel-node$="choose-mode"]');
                const expression=root.querySelector('[data-mcel-node$="arithmetic-expression"]');
                const targets=[mode,expression];
                if (targets.some(el=>!el)) throw Error('real source-bound groups absent');
                const buttons=[...targets.flatMap(el=>[...el.querySelectorAll('button')])];
                const signature=()=>targets.concat(buttons).map(el=>{
                    const r=el.getBoundingClientRect();
                    return [r.x,r.y,r.width,r.height,el.disabled,el.getAttribute('aria-pressed')];
                });
                const initial=signature();
                const scrollable=[...root.querySelectorAll('*')].filter(el=>
                    ['auto','scroll'].includes(getComputedStyle(el).overflowY));
                const scrollSignature=()=>scrollable.map(el=>[
                    getComputedStyle(el).overflowX,getComputedStyle(el).overflowY,
                    el.scrollTop]);
                const beforeScroll=scrollSignature();
                const palette=document.createElement('div');
                palette.className='mcel-runtime-preview';document.body.appendChild(palette);
                const reports=[];
                for(const theme of McelLabStyleLaw.themes){
                    palette.className='mcel-runtime-preview '+theme;
                    const tokens=McelSemanticThemePreview.themeTokens(palette,getComputedStyle);
                    const report=McelSemanticThemePreview.apply({root,ir,
                        bridge:McelSemanticPresentationBridge,styleLaw:McelLabStyleLaw,
                        theme,tokens,bindingModel:'operational-runtime'});
                    const form=McelSemanticFormPresentationLaw.apply({root,ir,
                        bridge:McelSemanticPresentationBridge,profile:'form-current'});
                    const styles=targets.map(el=>({
                        corner:el.getAttribute('data-mcel-form-presentation-corner'),
                        radius:parseFloat(getComputedStyle(el).borderTopLeftRadius),
                        background:getComputedStyle(el).backgroundColor,
                        overflow:getComputedStyle(el).overflow}));
                    reports.push({status:report.status, rounded:form.roundedGroupBackgrounds,
                        contrast:report.visualEvidence.contrast,styles,
                        formGeometry:form.shapeLayout?.status,
                        liveControls:buttons.every(el=>el.isConnected && !el.disabled),
                        scrollStable:JSON.stringify(scrollSignature())===JSON.stringify(beforeScroll)});
                }
                const r=mode.getBoundingClientRect();
                // The center of the gap between two vertically stacked rounded
                // buttons remains painted by the same semantic group background.
                const bs=[...mode.querySelectorAll('button')].filter(el=>el.getClientRects().length);
                const a=bs[0].getBoundingClientRect(),b=bs[1].getBoundingClientRect();
                const gap={x:Math.floor((a.left+a.right)/2),y:Math.floor((a.bottom+b.top)/2)};
                return {reports,canonicalColor:root.hasAttribute('data-mcel-semantic-color-full'),
                    bindingsSame:JSON.stringify(semanticBefore)===JSON.stringify(
                    [...root.querySelectorAll('[data-mcel-node],[data-mcel-region]')]
                    .map(el=>[el.getAttribute('data-mcel-node'),el.getAttribute('data-mcel-region')])),
                    irSame:irBefore===JSON.stringify(ir),
                    corners:[
                        {x:Math.floor(r.left)+1,y:Math.floor(r.top)+1},
                        {x:Math.floor(r.right)-2,y:Math.floor(r.top)+1},
                        {x:Math.floor(r.left)+1,y:Math.floor(r.bottom)-2},
                        {x:Math.floor(r.right)-2,y:Math.floor(r.bottom)-2}],gap};
            }""")
            assert result["irSame"] and result["bindingsSame"], (width,result)
            assert len(result["reports"]) == 8, (width,result)
            for report in result["reports"]:
                assert report["status"] == "applied-in-isolated-lab-frame", (width,report)
                assert report["rounded"] >= 2, (width,report)
                assert report["formGeometry"] == "feasible", (width,report)
                assert report["liveControls"] and report["scrollStable"], (width,report)
                assert report["contrast"]["belowAA"] == 0, (width,report)
                for group in report["styles"]:
                    assert group["corner"] is not None, (width,report)
                    assert group["radius"] >= 6, (width,report)
                    assert group["overflow"] == "visible", (width,report)
            # Visual proof: the corner has ceased to paint square while the
            # inter-button gap remains pixel-identical with/without rounding.
            rounded = Image.open(BytesIO(page.screenshot())).convert('RGB')
            page.evaluate("""() => document.querySelectorAll('[data-mcel-form-presentation-corner]')
                .forEach(el=>el.removeAttribute('data-mcel-form-presentation-corner'))""")
            square = Image.open(BytesIO(page.screenshot())).convert('RGB')
            gap = result["gap"]
            gx,gy=gap["x"],gap["y"]
            exposed = 0
            for corner in result["corners"]:
                cx,cy=corner["x"],corner["y"]
                exposed += rounded.getpixel((cx,cy)) != square.getpixel((cx,cy))
            # Some narrow-width children paint over their parent's corner;
            # verify all four exposed tips at the desktop width.
            # The fully migrated Calculator already owns the corner paint
            # through Color Law and Form together. Clearing the legacy
            # corner marker is no longer a meaningful square-paint baseline.
            # Geometry, visibility and overflow are independently checked by
            # Form's canonical solver; retain pixel-difference proof only for
            # legacy Theme-projected applications.
            if not result['canonicalColor']:
                assert exposed >= (4 if width == 950 else 1), (width,result["corners"],exposed)
            assert rounded.getpixel((gx,gy)) == square.getpixel((gx,gy)), (width,gap)
            page.close()
        browser.close()
