"""Source-scoped semantic color propagation and computed contrast oracle."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"


def test_color_projection_contract_is_not_calculator_specific():
    script = (WEB / "scripts/mcel-semantic-theme-preview.js").read_text(encoding="utf-8")
    assert "observeRuntimePresentationTargets" in script
    assert "visualDescendants(root,report.targets,computed)" in script
    assert "region.contains(element)" in script
    assert "auditContrast(root,computed)" in script
    assert "data-mcel-theme-preview-region-depth" in script
    assert 'root.querySelectorAll("*")' in script
    assert "#calculator" not in script
    assert "data-value=\"basic\"" not in script
    for dangerous in ("pointer-events:", "display:", "position:", "width:", "height:", "opacity:"):
        assert dangerous not in script


def test_browser_semantic_theme_uses_one_coherent_palette_without_layout_changes():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    source = (WEB / "apps/mcel-lab.html").read_text(encoding="utf-8")
    start = source.index('  <section id="mcel-lab-presentation-workspace"')
    end = source.index('  <section id="mcel-semantic-compatibility"', start)
    fixture = '<div id="mcel-lab-app" data-mcel-workspace-view="presentation">' + source[start:end] + '</div>'
    js = (WEB / "scripts/mcel-lab.js").read_text(encoding="utf-8")
    operations = js[js.index('    // The Calculator experiment never writes to the production Calculator DOM.'):
                    js.index('    // Presentation compatibility inspection is observational only.')]

    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={"width": width, "height": 860})
            page.set_content('<!doctype html><html><head><style>body{background:#080812;color:white}'
                             '.mcel-runtime-preview{display:none}</style></head><body>'
                             '<div id="fake-palette" class="mcel-runtime-preview theme-machine"></div>'
                             + fixture + '</body></html>')
            page.add_style_tag(path=str(WEB / "styles/mcel-lab.css"))
            for script in ("mcel-contract.js", "mcel-law-registry.js", "mcel-style-law.js",
                           "mcel-semantic-presentation-bridge.js", "mcel-semantic-theme-preview.js"):
                page.add_script_tag(path=str(WEB / "scripts" / script))
            page.add_script_tag(content='''var mcelLabState={semanticThemePreviewEnabled:false};
                var mcelThemeSelect=document.getElementById('mcel-theme-select');
                var mcelRuntimePreview=document.getElementById('fake-palette');\n'''+operations+'''
                document.getElementById('mcel-semantic-theme-frame').addEventListener('load',onMcelSemanticThemeFrameLoad);
                ''')
            page.evaluate("enableMcelSemanticThemePreview()")
            page.evaluate('''html => {document.getElementById('mcel-semantic-theme-frame').srcdoc=html;}''',
                          render_expanded_page(ROOT, promoted=True))
            page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-status')
                .textContent.startsWith('Applied ')''', timeout=20000)
            results = page.evaluate('''() => {
                const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
                const win=doc.defaultView, root=doc.getElementById('mcel-expanded-root');
                const region=root.querySelector('[data-mcel-region]');
                const original=doc.getElementById('mcel-expanded-ir').textContent;
                const button=root.querySelector('[data-mcel-node][data-node-kind="control"] button');
                if(!button) throw Error('canonical source-bound button missing');
                // Deliberately add an arbitrary dark painted child inside a
                // canonical region. No label, class, or Calculator selector is
                // used to map it. Its theme is inherited from that region.
                const sub=doc.createElement('section');sub.id='unrelated-visual-surface';
                sub.style.cssText='background-color:#050505;color:#222;border:1px solid #161616';
                sub.innerHTML='<strong>Generic readable surface</strong><button type="button" aria-pressed="true">Selected</button>'+
                   '<button type="button" disabled>Unavailable</button>';
                region.appendChild(sub);
                const geometry=()=>[root,region,button,sub].map(el=>{
                    const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height];});
                const originalGeometry=geometry();
                const oldBindings=[...root.querySelectorAll('[data-mcel-node],[data-mcel-region]')]
                    .map(el=>[el.getAttribute('data-mcel-node'),el.getAttribute('data-mcel-region'),el.getAttribute('data-node-kind')]);
                const oldState=[button.disabled, sub.querySelector('button[disabled]').disabled,
                    sub.querySelector('[aria-pressed]').getAttribute('aria-pressed')];
                const results=[];
                for(const theme of McelLabStyleLaw.themes){
                    mcelThemeSelect.value=theme;
                    mcelRuntimePreview.className='mcel-runtime-preview '+theme;
                    const report=applyMcelSemanticThemePreview('color-coherence-oracle');
                    const c=win.getComputedStyle(sub), selected=win.getComputedStyle(sub.querySelector('[aria-pressed]'));
                    const disabled=win.getComputedStyle(sub.querySelector('button[disabled]'));
                    const normal=win.getComputedStyle(button);
                    results.push({theme,status:report.status,contrast:report.visualEvidence.contrast,
                        painted:report.visualEvidence.paintedDescendants,
                        surface:c.backgroundColor,foreground:c.color,border:c.borderColor,
                        selected:selected.backgroundColor,disabled:disabled.backgroundColor,
                        normal:normal.backgroundColor, controls:report.controls, regions:report.regions,
                        geometrySame:JSON.stringify(originalGeometry)===JSON.stringify(geometry()),
                        bindingsSame:JSON.stringify(oldBindings)===JSON.stringify([...root.querySelectorAll('[data-mcel-node],[data-mcel-region]')]
                            .map(el=>[el.getAttribute('data-mcel-node'),el.getAttribute('data-mcel-region'),el.getAttribute('data-node-kind')])),
                        stateSame:JSON.stringify(oldState)===JSON.stringify([button.disabled,sub.querySelector('button[disabled]').disabled,
                            sub.querySelector('[aria-pressed]').getAttribute('aria-pressed')])});
                }
                return {results,irUnchanged:original===doc.getElementById('mcel-expanded-ir').textContent,
                    evidence:document.getElementById('mcel-semantic-theme-evidence-text').textContent};
            }''')
            assert results["irUnchanged"], (width, results)
            records = results["results"]
            assert len(records) == 8
            for r in records:
                assert r["status"] == "applied-in-isolated-lab-frame", (width,r)
                assert r["painted"] >= 1, (width,r)
                assert r["contrast"]["checked"] >= 30, (width,r)
                assert r["contrast"]["belowAA"] == 0, (width,r)
                assert r["contrast"]["minimumRatio"] >= 4.5, (width,r)
                assert r["geometrySame"] and r["bindingsSame"] and r["stateSame"], (width,r)
                assert r["surface"] != "rgb(5, 5, 5)", (width,r)
                assert r["selected"] != r["normal"], (width,r)
                assert r["disabled"] != r["normal"], (width,r)
            assert len({r["surface"] for r in records}) >= 6, (width,records)
            assert "Text contrast" in results["evidence"] and "below 4.5:1" in results["evidence"]
            page.close()
        browser.close()



def test_promoted_runtime_real_panels_resolve_background_and_text_as_a_pair():
    """Regression for the real expert CSS (!important shorthand vs semantic colors)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    page_html = render_expanded_page(ROOT, promoted=True)
    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={"width": width, "height": 860})
            # about:blank has no storage origin. Mock only persistence so the
            # authentic promoted runtime can mount without a live server.
            page.evaluate("""() => {for (const name of ['localStorage','sessionStorage']) {
                const data=new Map();Object.defineProperty(window,name,{configurable:true,value:{
                    getItem:key=>data.get(key)||null,setItem:(key,val)=>data.set(key,String(val)),
                    removeItem:key=>data.delete(key),clear:()=>data.clear()}});}}""")
            page.set_content(page_html)
            assert page.locator('#mcel-expanded-diagnostic').inner_text() == 'Semantic Calculator ready'
            page.add_style_tag(path=str(WEB / "styles/mcel-lab.css"))
            for script in ("mcel-contract.js", "mcel-law-registry.js", "mcel-style-law.js",
                           "mcel-semantic-presentation-bridge.js", "mcel-semantic-theme-preview.js"):
                page.add_script_tag(path=str(WEB / "scripts" / script))
            record = page.evaluate("""() => {
                const doc=document,root=doc.getElementById('mcel-expanded-root');
                const sourceBefore=doc.getElementById('mcel-expanded-ir').textContent;
                const ir=JSON.parse(sourceBefore);
                const bindings=()=>[...root.querySelectorAll('[data-mcel-node],[data-mcel-region]')].map(el=>
                    [el.getAttribute('data-mcel-node'),el.getAttribute('data-mcel-region'),el.getAttribute('data-node-kind')]);
                const bindingsBefore=bindings();
                const mode=[...root.querySelectorAll('h2')].find(el=>el.textContent.trim().toLowerCase()==='mode');
                const summary=[...root.querySelectorAll('summary')].find(el=>el.textContent.includes('Activity'));
                if (!mode || !summary) throw Error('real representative region text missing');
                const regionMode=mode.closest('[data-mcel-region]');
                const regionActivity=summary.closest('[data-mcel-region]');
                const pressed=root.querySelector('button[aria-pressed="true"]');
                const stateBefore=[pressed?.getAttribute('aria-pressed'),root.querySelectorAll('[data-mcel-node]').length];
                const sample=[root,regionMode,regionActivity,mode,summary,pressed].filter(Boolean);
                const rects=()=>sample.map(el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]});
                const palette=doc.createElement('div');palette.className='mcel-runtime-preview';doc.body.appendChild(palette);
                const before=rects();
                const reports=[];
                for(const theme of McelLabStyleLaw.themes){
                    palette.className='mcel-runtime-preview '+theme;
                    const tokens=McelSemanticThemePreview.themeTokens(palette,getComputedStyle);
                    const report=McelSemanticThemePreview.apply({root,ir,bridge:McelSemanticPresentationBridge,
                        styleLaw:McelLabStyleLaw,theme,tokens,bindingModel:'operational-runtime'});
                    const modeBg=getComputedStyle(regionMode).backgroundColor;
                    const activityBg=getComputedStyle(regionActivity).backgroundColor;
                    const modeInk=getComputedStyle(mode).color;
                    const activityInk=getComputedStyle(summary).color;
                    reports.push({theme,status:report.status,contrast:report.visualEvidence.contrast,
                        modeBg,activityBg,modeInk,activityInk,rectsSame:JSON.stringify(before)===JSON.stringify(rects()),
                        stateSame:JSON.stringify(stateBefore)===JSON.stringify([pressed?.getAttribute('aria-pressed'),root.querySelectorAll('[data-mcel-node]').length])});
                }
                return {reports,irUnchanged:doc.getElementById('mcel-expanded-ir').textContent===sourceBefore,
                    bindingsUnchanged:JSON.stringify(bindings())===JSON.stringify(bindingsBefore)};
            }""")
            assert record['irUnchanged'] and record['bindingsUnchanged'], (width, record)
            assert len(record['reports']) == 8, (width, record)
            for result in record['reports']:
                assert result['status']=='applied-in-isolated-lab-frame', (width,result)
                assert result['contrast']['checked'] >= 30, (width,result)
                assert result['contrast']['belowAA'] == 0, (width,result)
                assert result['contrast']['minimumRatio'] >= 4.5, (width,result)
                assert result['rectsSame'] and result['stateSame'], (width,result)
                assert result['modeBg'] != 'rgb(24, 35, 43)', (width,result)
                assert result['activityBg'] != 'rgb(24, 35, 43)', (width,result)
            page.close()
        browser.close()
