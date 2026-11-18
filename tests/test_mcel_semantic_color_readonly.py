"""MCEL Color Law v1: palette-backed, observational semantic resolution.

This baby step adds no application consumer and must not change any prior
CSS painting, Calculator state, geometry or diagnostic widget colors.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"
SCRIPTS = WEB / "scripts"
CSS = WEB / "styles/mcel-lab.css"


def test_source_contract_is_non_mutating_and_uses_existing_theme_authority():
    code = (SCRIPTS / "mcel-style-law.js").read_text(encoding="utf-8")
    lab = (SCRIPTS / "mcel-lab.js").read_text(encoding="utf-8")
    html = (WEB / "apps/mcel-lab.html").read_text(encoding="utf-8")
    assert "function resolveColor(" in code
    assert "function semanticPaletteFor(" in code
    assert "const semanticColorRoles" in code
    assert "return Object.freeze({schema:semanticColorSchema,status:\"resolved\"" in code
    assert "semanticPaletteTokens" in code
    assert 'id="mcel-semantic-color-inspector"' in html
    assert '<details id="mcel-semantic-color-inspector"' in html
    assert "renderMcelSemanticColorInspector" in lab
    assert "if (!inspector?.open) return null;" in lab
    assert "inspector-results" in lab
    assert "applyMcelSemanticThemePreview(\"theme-change\");" in lab
    assert "renderMcelSemanticColorInspector();" in lab
    # This patch neither migrates consumers nor edits the old projector.
    assert "McelUnifiedPresentationController?.applyLabTheme" in lab
    assert "window.McelLabStyleLaw" in (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8") or \
        "applications/scripts/mcel-style-law.js" in (ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")


def test_only_additive_css_palette_tokens_for_all_eight_themes():
    css = CSS.read_text(encoding="utf-8")
    names = ("theme-machine", "theme-local", "theme-saas", "theme-editorial",
             "theme-luxury", "theme-civic", "theme-accessible", "theme-debug")
    for theme in names:
        i = css.index(f".mcel-runtime-preview.{theme} {{")
        end = css.index("\n}", i)
        block = css[i:end]
        for meaning in ("error", "warning", "success"):
            assert len(re.findall(rf"--mcel-theme-{meaning}:\s*#[0-9a-f]{{6}};", block)) == 1
    for meaning in ("error", "warning", "success"):
        # Nobody consumes the new variables in this stage; Color Law only reads.
        assert css.count(f"var(--mcel-theme-{meaning})") == 0


def test_semantic_resolution_is_deterministic_and_refuses_missing_meaning():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        page = browser.new_page()
        page.set_content('<div id="palette" class="mcel-runtime-preview theme-machine"></div>')
        page.add_style_tag(path=str(CSS))
        for name in ("mcel-contract.js", "mcel-law-registry.js", "mcel-style-law.js"):
            page.add_script_tag(path=str(SCRIPTS / name))
        report = page.evaluate("""() => {
          const law=McelLabStyleLaw,el=document.getElementById('palette');
          const runs=[];
          for(const theme of law.themes){
            el.className='mcel-runtime-preview '+theme;
            const palette=law.semanticPaletteFor(el,getComputedStyle);
            const resolved=[];
            for(const role of law.semanticColorRoles){
              const surface=role.startsWith('diagnostic.')?'indicator':
                role.startsWith('action.')?'control':role.startsWith('navigation.')?'navigation':
                role.startsWith('text.')?'text':'panel';
              const value=law.resolveColor({theme,role,state:'enabled',surface,palette});
              const same=law.resolveColor({theme,role,state:'enabled',surface,palette});
              resolved.push({role,status:value.status,stable:JSON.stringify(value)===JSON.stringify(same),
                fg:value.foreground,bg:value.background,border:value.border,accent:value.accent});
            }
            const diagnostic=resolved.filter(x=>x.role.startsWith('diagnostic.') && x.role!=='diagnostic.info');
            const stopped=law.resolveColor({theme,role:'action.stop',state:'enabled',surface:'control',palette});
            const disabled=law.resolveColor({theme,role:'action.stop',state:'disabled',surface:'control',palette});
            const selected=law.resolveColor({theme,role:'navigation.selected',state:'selected',surface:'navigation',palette});
            const pending=law.resolveColor({theme,role:'action.start',state:'pending',surface:'control',palette});
            runs.push({theme,palette:!!palette,roles:resolved.length,
              rolesComplete:resolved.every(x=>x.status==='resolved' && x.stable && x.fg && x.bg && x.border && x.accent),
              signalCount:new Set(diagnostic.map(x=>x.fg)).size,
              borderCount:new Set(diagnostic.map(x=>x.border)).size,
              disabledForeground:disabled.foreground,enabledForeground:stopped.foreground,
              disabledBorder:disabled.border,enabledBorder:stopped.border,
              selectedBackground:selected.background,pendingBackground:pending.background,
              report:diagnostic});
          }
          const ref=law.resolveColor({theme:'theme-machine',role:'diagnostic.error'});
          return {runs,roles:law.semanticColorRoles.length,states:law.semanticColorStates.length,
            defaultExpr:ref.foreground,
            refusals:[
              law.resolveColor({theme:'unknown',role:'diagnostic.error'}).reason,
              law.resolveColor({theme:'theme-machine',role:'diagnostic.unexpected'}).reason,
              law.resolveColor({theme:'theme-machine',role:'diagnostic.error',state:'bad'}).reason,
              law.resolveColor({theme:'theme-machine',role:'diagnostic.error',surface:'background'}).reason,
              law.resolveColor({theme:'theme-machine',role:'diagnostic.error',palette:{}}).reason
            ],
            canonicalTheme:law.resolveColor({theme:'original',role:'diagnostic.warning'}).theme,
            themeCount:law.themes.length};
        }""")
        browser.close()
    assert report["themeCount"] == 8
    assert report["roles"] >= 14 and report["states"] >= 5
    assert report["defaultExpr"].startswith("var(--mcel-theme-")
    assert report["canonicalTheme"] == "theme-machine"
    assert report["refusals"] == ["unknown-theme", "unknown-role", "unknown-state",
                                  "unknown-surface", "incomplete-theme-palette"]
    for rec in report["runs"]:
        assert rec["palette"] and rec["roles"] == report["roles"] and rec["rolesComplete"], rec
        assert rec["signalCount"] == rec["borderCount"] == 3, rec
        assert rec["disabledBorder"] != rec["enabledBorder"], rec
        assert rec["selectedBackground"] != rec["pendingBackground"], rec


def test_read_only_lab_inspector_does_not_modify_preview_or_ewg_colors():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    html = (WEB / "apps/mcel-lab.html").read_text(encoding="utf-8")
    script = (SCRIPTS / "mcel-lab.js").read_text(encoding="utf-8")
    operation = script[script.index("    // Observational semantic Color Law inspection."):
                       script.index("    function initMcelLabApp()", script.index("    // Observational semantic Color Law inspection."))]
    fixture = '''<!doctype html><html><body><div id="mcel-lab-app" style="display:block!important">
      <div id="mcel-runtime-preview" class="mcel-runtime-preview theme-machine">
        <button id="operational-button">Evaluate</button>
        <div id="ewg" class="mcel-diagnostics-counter">
          <span class="mcel-diagnostics-counter__error">0</span>
          <span class="mcel-diagnostics-counter__warning">3</span>
          <span class="mcel-diagnostics-counter__ok">2</span>
        </div>
      </div>'''
    fixture += html[html.index('<label>\n      Theme'):html.index('    <label>\n      Form')]
    fixture += html[html.index('    <details id="mcel-semantic-color-inspector"'):
                    html.index('  </section>\n  <section class="mcel-site-skeleton"')]
    fixture += '</div></body></html>'

    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        page = browser.new_page(viewport={"width": 1100, "height": 820})
        page.set_content(fixture)
        page.add_style_tag(path=str(CSS))
        page.add_style_tag(path=str(WEB / "styles/mcel-diagnostics-counter-widget.css"))
        for name in ("mcel-contract.js", "mcel-law-registry.js", "mcel-style-law.js"):
            page.add_script_tag(path=str(SCRIPTS / name))
        page.add_script_tag(content='''var mcelLabState={theme:'theme-machine'};
          var mcelThemeSelect=document.getElementById('mcel-theme-select');
          var mcelRuntimePreview=document.getElementById('mcel-runtime-preview');\n''' + operation)
        report = page.evaluate("""() => {
          const preview=document.getElementById('mcel-runtime-preview');
          const ewg=document.querySelectorAll('#ewg span');
          const control=document.getElementById('operational-button');
          const inspect=document.getElementById('mcel-semantic-color-inspector');
          const initial=()=>({control:getComputedStyle(control).color,
            background:getComputedStyle(control).backgroundColor,
            rect:(()=>{const r=control.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})(),
            ewg:[...ewg].map(el=>getComputedStyle(el).color)});
          const before=initial();
          const closed=renderMcelSemanticColorInspector();
          inspect.open=true;
          const machine=renderMcelSemanticColorInspector();
          const rows=document.querySelectorAll('#mcel-semantic-color-inspector tbody tr').length;
          const machineError=document.querySelector('#mcel-semantic-color-inspector-results').textContent;
          const after=initial();
          mcelThemeSelect.value='theme-civic';
          preview.className='mcel-runtime-preview theme-civic';
          const civicBefore=initial();
          const civic=renderMcelSemanticColorInspector();
          const civicAfter=initial();
          return {closed,machine,civic,rows,containsError:machineError.includes('diagnostic.error'),
            before,after,civicBefore,civicAfter,
            modifiedElements:document.querySelectorAll('[data-mcel-color-law-applied]').length};
        }""")
        browser.close()
    assert report["closed"] is None
    assert report["machine"]["status"] == report["civic"]["status"] == "observed"
    assert report["rows"] == report["machine"]["roles"] >= 14
    assert report["containsError"]
    assert report["before"] == report["after"]
    assert report["civicBefore"] == report["civicAfter"]
    assert report["modifiedElements"] == 0


def test_proposed_diagnostic_colors_keep_contrast_under_all_themes():
    """New palette semantics are distinguishable and readable before any consumer uses them."""
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content('<div id="palette" class="mcel-runtime-preview theme-machine">'
                         '<span id="sample">Diagnostic</span></div>')
        page.add_style_tag(path=str(CSS))
        for name in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js'):
            page.add_script_tag(path=str(SCRIPTS / name))
        report = page.evaluate('''() => {
          const law=McelLabStyleLaw,el=document.getElementById('palette'),
            sample=document.getElementById('sample');
          const ctx=document.createElement('canvas').getContext('2d');
          const rgb=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;
            ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3);};
          const luminance=color=>color.map(v=>{v/=255;
            return v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4;
          }).reduce((sum,v,i)=>sum+v*[0.2126,0.7152,0.0722][i],0);
          const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);
            return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05);};
          return law.themes.map(theme=>{
            el.className='mcel-runtime-preview '+theme;
            const palette=law.semanticPaletteFor(el,getComputedStyle);
            return {theme,ratios:['error','warning','success'].map(signal=>{
              const v=law.resolveColor({theme,role:'diagnostic.'+signal,
                state:'enabled',surface:'indicator',palette});
              sample.style.color=v.foreground;
              sample.style.backgroundColor=v.background;
              return {signal,ratio:contrast(rgb(getComputedStyle(sample).color),
                rgb(getComputedStyle(sample).backgroundColor))};
            })};
          });
        }''')
        browser.close()
    assert len(report) == 8
    for theme in report:
        assert all(value['ratio'] >= 4.5 for value in theme['ratios']), theme
