"""Calculator-preview UI acceptance: primary surface, theme color and state continuity."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_calculator_host_projection import project as calculator_markup

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer/web/applications"


def _lab_fixture():
    html = (WEB / "apps/mcel-lab.html").read_text()
    start = html.index('  <section id="mcel-lab-presentation-workspace"')
    end = html.index('  <section id="mcel-semantic-compatibility"', start)
    return '<section id="mcel-lab-app" data-mcel-workspace-view="presentation">' + html[start:end] + '</section>'


def test_active_preview_contract_is_one_primary_surface():
    html = (WEB / "apps/mcel-lab.html").read_text()
    lab_js = (WEB / "scripts/mcel-lab.js").read_text()
    css = (WEB / "styles/mcel-lab.css").read_text()
    assert html.index('id="mcel-theme-select"') < html.index('id="mcel-semantic-theme-frame"')
    assert html.index('id="mcel-semantic-theme-frame"') < html.index('id="mcel-legacy-site-preview"')
    assert 'Chrome is not yet applied to modern MCEL' in html
    assert 'setMcelSemanticThemePreviewMode(true);' in lab_js
    assert 'setMcelSemanticThemePreviewMode(false);' in lab_js
    assert 'legacy.hidden = Boolean(enabled)' in lab_js
    assert 'visualEvidence' in (WEB / 'scripts/mcel-semantic-theme-preview.js').read_text()
    assert '#mcel-lab-app #mcel-legacy-site-preview[hidden]' in css


def test_preview_browser_visible_colors_switch_without_semantic_or_geometry_changes():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    compiled = compile_dsl_application(ROOT / "mcel_apps/calculator/application.js")
    assert compiled.valid, compiled.diagnostics
    ir = compiled.normalized_ir
    lab_js = (WEB / "scripts/mcel-lab.js").read_text()
    start = lab_js.index('    // The Calculator experiment never writes to the production Calculator DOM.')
    end = lab_js.index('    // Presentation compatibility inspection is observational only.', start)
    operations = lab_js[start:end]
    framed = ('<!doctype html><html><head><style>:root{--gold:#f6c75b;--ink:#f6f6ee;'
              '--line:#666;--mint:#9ed69f;--muted:#888}body{margin:0}'
              + (WEB / 'styles/calculator.css').read_text()
              + '#calculator-app{display:block!important;min-height:240px}</style></head><body>'
              + calculator_markup(ROOT) + '</body></html>')
    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={"width": width, "height": 940})
            page.set_content('<html><head><style>body{margin:0;background:#060811;color:#fff}'
                             '#mcel-lab-app{display:block!important;min-height:100vh}'
                             '.mcel-runtime-preview{display:none}</style></head><body>'
                             '<div id="fake-mcel-runtime-preview" class="mcel-runtime-preview theme-machine"></div>'
                             + _lab_fixture() + '</body></html>')
            page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
            for script in ('mcel-contract.js', 'mcel-law-registry.js', 'mcel-style-law.js',
                           'mcel-semantic-presentation-bridge.js', 'mcel-semantic-theme-preview.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / script))
            setup = '''var mcelLabState={semanticThemePreviewEnabled:false};
                       var mcelThemeSelect=document.getElementById('mcel-theme-select');
                       var mcelRuntimePreview=document.getElementById('fake-mcel-runtime-preview');'''
            page.add_script_tag(content=setup + '\n' + operations)
            page.evaluate('''({ir,framed}) => {
              window.McelLabCanonicalPresentationSources = {calculator: ir};
              window.__frameFixture = framed;
              document.getElementById('mcel-semantic-theme-enable').addEventListener('click',enableMcelSemanticThemePreview);
              document.getElementById('mcel-semantic-theme-disable').addEventListener('click',disableMcelSemanticThemePreview);
            }''', {'ir': ir, 'framed': framed})
            assert page.locator('#mcel-legacy-site-preview').is_visible()
            page.locator('#mcel-semantic-theme-enable').click()
            assert not page.locator('#mcel-legacy-site-preview').is_visible()
            assert page.locator('#mcel-semantic-theme-frame').is_visible()
            assert page.locator('.mcel-semantic-theme-chrome-notice').is_visible()
            page.evaluate('''() => {
              const frame=document.getElementById('mcel-semantic-theme-frame');
              frame.srcdoc=window.__frameFixture;
            }''')
            page.wait_for_function("document.querySelector('#mcel-semantic-theme-frame').contentDocument?.querySelector('#calculator-app')")
            result = page.evaluate('''() => {
              const frame=document.getElementById('mcel-semantic-theme-frame');
              const doc=frame.contentDocument;
              const el=doc.getElementById('calculator-mode-basic');
              const shell=doc.querySelector('.calculator-shell');
              const beforeRect=[el.getBoundingClientRect().x,el.getBoundingClientRect().y,el.getBoundingClientRect().width,el.getBoundingClientRect().height];
              const beforeSemantic=el.getAttribute('data-mcel-control');
              const beforeDisabled=el.disabled;
              const results=[];
              for (const theme of ['theme-machine','theme-civic','theme-accessible']) {
                mcelThemeSelect.value=theme;
                mcelRuntimePreview.className='mcel-runtime-preview '+theme;
                applyMcelSemanticThemePreview('ui-acceptance');
                const colors=doc.defaultView.getComputedStyle(el);
                results.push({theme,control:colors.backgroundColor,ink:colors.color,
                  region:doc.defaultView.getComputedStyle(shell).backgroundColor,
                  evidence:document.getElementById('mcel-semantic-theme-evidence-text').textContent,
                  status:document.getElementById('mcel-semantic-theme-status').textContent});
              }
              return {results, beforeRect,afterRect:[el.getBoundingClientRect().x,el.getBoundingClientRect().y,el.getBoundingClientRect().width,el.getBoundingClientRect().height],
                beforeSemantic,afterSemantic:el.getAttribute('data-mcel-control'),beforeDisabled,afterDisabled:el.disabled,
                selected:mcelThemeSelect.value,
                previewWidth:frame.getBoundingClientRect().width,
                bodyWidth:document.documentElement.clientWidth,
                themeAboveFrame:document.getElementById('mcel-theme-select').getBoundingClientRect().top < frame.getBoundingClientRect().top};
            }''')
            assert all('Applied ' in r['status'] and 'source-bound controls' in r['evidence']
                       for r in result['results']), (width, result)
            assert len({r['control'] for r in result['results']}) == 3, (width, result)
            assert len({r['region'] for r in result['results']}) == 3, (width, result)
            assert result['beforeRect'] == result['afterRect'], (width, result)
            assert result['beforeSemantic'] == result['afterSemantic']
            assert result['beforeDisabled'] == result['afterDisabled']
            assert result['themeAboveFrame']
            assert result['previewWidth'] <= min(result['bodyWidth'], 960) + 2, (width, result)
            page.locator('#mcel-semantic-theme-disable').click()
            assert page.locator('#mcel-legacy-site-preview').is_visible()
            assert not page.locator('#mcel-semantic-theme-frame').is_visible()
            page.close()
        browser.close()


def test_theme_preview_waits_for_late_canonical_mount_and_reports_missing_source():
    """Browser oracle: iframe onload can precede the canonical application mount."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    ir = compile_dsl_application(ROOT / "mcel_apps/calculator/application.js").normalized_ir
    host = calculator_markup(ROOT)
    lab_js = (WEB / "scripts/mcel-lab.js").read_text()
    operations = lab_js[lab_js.index('    // The Calculator experiment never writes to the production Calculator DOM.'):
                        lab_js.index('    // Presentation compatibility inspection is observational only.')]
    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        page.set_content('<html><body><div id="theme-palette" class="mcel-runtime-preview theme-machine"></div>'
                         + _lab_fixture() + '</body></html>')
        page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
        for script in ('mcel-contract.js', 'mcel-law-registry.js', 'mcel-style-law.js',
                       'mcel-semantic-presentation-bridge.js', 'mcel-semantic-theme-preview.js'):
            page.add_script_tag(path=str(WEB / 'scripts' / script))
        page.add_script_tag(content='''var mcelLabState={semanticThemePreviewEnabled:true};
            var mcelThemeSelect=document.getElementById('mcel-theme-select');
            var mcelRuntimePreview=document.getElementById('theme-palette');\n''' + operations)
        page.evaluate('''() => {
            const f=document.getElementById('mcel-semantic-theme-frame');
            f.hidden=false;
            f.srcdoc='<!doctype html><html><body><p id="loading">Mount pending</p></body></html>';
        }''')
        page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-frame').contentDocument?.getElementById('loading')''')
        # First prove an absent canonical source is not mistaken for a missing DOM.
        page.evaluate('onMcelSemanticThemeFrameLoad()')
        assert 'Canonical Calculator semantic source is absent' in page.locator('#mcel-semantic-theme-status').inner_text()
        # Provide the genuine compiled source, but insert the Calculator *after*
        # the simulated frame load. The observer must finish the binding.
        page.evaluate('''ir => {const frame=document.getElementById('mcel-semantic-theme-frame');
            frame.contentWindow.McelLabCanonicalPresentationSources={calculator: ir};
            onMcelSemanticThemeFrameLoad();}''', ir)
        assert 'Waiting for canonical Calculator root' in page.locator('#mcel-semantic-theme-status').inner_text()
        page.evaluate('''() => {
            const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
            doc.body.insertAdjacentHTML('beforeend','<section id="calculator-app"></section>');
        }''')
        page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-status')
            .textContent.includes('Waiting for Calculator semantic bindings')''')
        page.evaluate('''host => {
            const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
            doc.getElementById('calculator-app').outerHTML=host;
        }''', host)
        page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-status')
            .textContent.startsWith('Applied Original MCEL')''', timeout=10000)
        result = page.evaluate('''() => {
            const doc=document.getElementById('mcel-semantic-theme-frame').contentDocument;
            const target=doc.getElementById('calculator-mode-basic');
            const machine=doc.defaultView.getComputedStyle(target).backgroundColor;
            mcelThemeSelect.value='theme-civic';
            mcelRuntimePreview.className='mcel-runtime-preview theme-civic';
            applyMcelSemanticThemePreview('theme-change');
            const civic=doc.defaultView.getComputedStyle(target).backgroundColor;
            mcelThemeSelect.value='theme-debug';
            mcelRuntimePreview.className='mcel-runtime-preview theme-debug';
            applyMcelSemanticThemePreview('theme-change');
            const debug=doc.defaultView.getComputedStyle(target).backgroundColor;
            return {machine,civic,debug,source:target.getAttribute('data-mcel-control'),
                status:document.getElementById('mcel-semantic-theme-status').textContent,
                coverage:document.getElementById('mcel-semantic-theme-evidence-text').textContent,
                marked:target.hasAttribute('data-mcel-theme-preview-kind')};
        }''')
        assert len({result['machine'], result['civic'], result['debug']}) == 3, result
        assert result['marked']
        assert result['source']  # Unchanged canonical DOM binding.
        assert result['status'].startswith('Applied Debug Wireframe'), result
        assert 'source-bound controls' in result['coverage']
        browser.close()
