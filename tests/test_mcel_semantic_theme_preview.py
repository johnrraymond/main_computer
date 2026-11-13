"""MCEL Baby Step 2: isolated theme projection from canonical Calculator semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_lab_semantic_presentation_source import project as projection_script
from main_computer.mcel_calculator_host_projection import project as calculator_markup

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
SCRIPTS = WEB / 'scripts'


def canonical_ir():
    result = compile_dsl_application(ROOT / 'mcel_apps/calculator/application.js')
    assert result.valid, result.diagnostics
    return result.normalized_ir


def test_lab_projection_is_directly_derived_from_authoritative_dsl_ir():
    script = projection_script(ROOT)
    assert script.startswith('window.McelLabCanonicalPresentationSources=Object.freeze({calculator:')
    assert 'hostMarkup' not in script
    assert 'semanticSurface' in script
    assert 'intent:calculator.' in script
    assert 'from main_computer.mcel_lab_semantic_presentation_source import project' in (ROOT / 'main_computer/viewport_pages.py').read_text()
    # Source is compiled, not copied into a separately maintained JSON definition.
    payload = json.loads(script[len('window.McelLabCanonicalPresentationSources=Object.freeze({calculator:'):-4])
    authority = canonical_ir()
    assert payload['schema'] == authority['schema']
    assert payload['intents'] == authority['intents']
    assert payload['fingerprints'] == authority['fingerprints']
    assert payload['surfaces'][0]['nodes'] == authority['surfaces'][0]['nodes']
    for key in ('controls', 'regions'):
        assert payload['surfaces'][0]['semanticSurface'][key] == authority['surfaces'][0]['semanticSurface'][key]


def test_full_app_template_expands_lab_semantics_without_game_bundle():
    # This export snapshot intentionally excludes the separate game bundle.
    # Stub only the unrelated game include so the actual application page and
    # virtual canonical IR include are exercised without changing app code.
    import subprocess
    import sys
    import textwrap
    check = textwrap.dedent("""
    from main_computer import game_web_loader
    game_web_loader.read_game_bundle = lambda *_a,**_k: ''
    from main_computer.viewport_pages import APPLICATIONS_INDEX_HTML
    text = APPLICATIONS_INDEX_HTML
    assert text.count('window.McelLabCanonicalPresentationSources=') == 1
    assert text.index('var McelSemanticPresentationBridge') < text.index('window.McelLabCanonicalPresentationSources=') < text.index('var McelSemanticThemePreview') < text.index('function initMcelLabApp')
    assert 'mcel-semantic-theme-enable' in text
    """)
    result = subprocess.run([sys.executable, '-c', check], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_lab_opt_in_only_and_no_production_theme_or_chrome_mutation():
    html = (WEB / 'apps/mcel-lab.html').read_text()
    lab = (SCRIPTS / 'mcel-lab.js').read_text()
    css_projection = (SCRIPTS / 'mcel-semantic-theme-preview.js').read_text()
    app = (ROOT / 'main_computer/web/applications.html').read_text()
    assert 'id="mcel-semantic-theme-enable"' in html
    assert 'id="mcel-semantic-theme-disable" disabled' in html
    assert 'id="mcel-semantic-theme-frame"' in html
    assert 'src="about:blank" sandbox=' in html
    assert 'mcelLabState.semanticThemePreviewEnabled = true;' in lab
    assert 'mcelLabState.semanticThemePreviewEnabled = false;' in lab
    assert "applyMcelSemanticThemePreview(\"theme-change\")" in lab
    assert 'applyMcelSemanticThemePreview("chrome-change")' not in lab
    assert 'styleLaw?.themes?.includes(theme)' in css_projection
    assert 'data-mcel-theme-preview-operation' in css_projection
    assert 'color-mix' in css_projection
    assert 'McelLabStyleLaw.applyRuntimeLaw' not in css_projection
    assert 'McelLabChromeLaw' not in css_projection
    assert 'mcel-lab-canonical-presentation-source.js' in app
    assert 'mcel-semantic-theme-preview.js' in app


def test_canonical_calculator_color_projection_browser():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    markup = calculator_markup(ROOT)
    ir = canonical_ir()
    # Use the real compiled host markup; omit Calculator app scripts so no live
    # network requests or evaluation effects can be executed by this oracle.
    page_html = ('<!doctype html><html><head><style>body{margin:0} '
                 '#calculator-app{display:block;height:820px} '
                 '.mcel-runtime-preview{display:none}</style></head><body>'
                 '<div id="theme-palette" class="mcel-runtime-preview theme-machine"></div>'
                 + markup + '</body></html>')
    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            page.set_content(page_html)
            page.add_style_tag(path=str(WEB / 'styles/calculator.css'))
            page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
            page.add_script_tag(path=str(SCRIPTS / 'mcel-semantic-presentation-bridge.js'))
            page.add_script_tag(path=str(SCRIPTS / 'mcel-semantic-theme-preview.js'))
            # A real style-law catalog is used, not an alternative list of themes.
            page.add_script_tag(path=str(SCRIPTS / 'mcel-contract.js'))
            page.add_script_tag(path=str(SCRIPTS / 'mcel-law-registry.js'))
            page.add_script_tag(path=str(SCRIPTS / 'mcel-style-law.js'))
            result = page.evaluate('''ir => {
              const root = document.getElementById('calculator-app');
              const palette = document.getElementById('theme-palette');
              const bridge = McelSemanticPresentationBridge;
              const preview = McelSemanticThemePreview;
              const targets = bridge.observePresentationTargets({ir,root});
              const elements = ['calculator-mode-basic', 'calculator-ask-model', 'calculator-graph-draw'];
              const rect = id => {
                const r = document.getElementById(id).getBoundingClientRect();
                return [r.x,r.y,r.width,r.height];
              };
              const before = elements.map(rect);
              const attrBefore = elements.map(id => document.getElementById(id).getAttribute('data-mcel-control'));
              const disabledBefore = elements.map(id => document.getElementById(id).disabled);
              const render = theme => {
                palette.className = 'mcel-runtime-preview ' + theme;
                const tokens = preview.themeTokens(palette, getComputedStyle);
                const report = preview.apply({root,ir,bridge,styleLaw:McelLabStyleLaw,theme,tokens});
                const color = getComputedStyle(document.getElementById('calculator-mode-basic'));
                return {report, background: color.backgroundColor, border: color.borderColor,
                        shell: getComputedStyle(document.querySelector('.calculator-shell')).backgroundColor};
              };
              const machine = render('theme-machine');
              const civic = render('theme-civic');
              const accessible = render('theme-accessible');
              const after = elements.map(rect);
              const attrAfter = elements.map(id => document.getElementById(id).getAttribute('data-mcel-control'));
              const disabledAfter = elements.map(id => document.getElementById(id).disabled);
              const conflict = structuredClone(ir);
              conflict.intents.push({...conflict.intents[0]});
              const conflicted = bridge.observePresentationTargets({ir:conflict,root});
              const fake = document.createElement('button');
              fake.textContent = 'Start';
              root.appendChild(fake);
              const fakePresent = bridge.observePresentationTargets({ir,root}).targets.some(t=>t.element===fake);
              fake.remove();
              return {sourceStatus:targets.status, controls:targets.targets.filter(t=>t.kind==='control').length,
                regions:targets.targets.filter(t=>t.kind==='region').length,
                sourceDiagnostics:targets.diagnostics,
                machine,civic,accessible,before,after,attrBefore,attrAfter,disabledBefore,disabledAfter,
                fakePresent,conflictIgnored:conflicted.targets.every(t=>t.kind!=='control'||t.intentId!==conflict.intents[0].id),
                themeClasses: [...root.classList]};
            }''', ir)
            assert result['sourceStatus'] == 'source-bound', (width, result['sourceDiagnostics'])
            assert result['controls'] >= 12, (width, result)
            assert result['regions'] >= 9, (width, result)
            for theme in ('machine','civic','accessible'):
                assert result[theme]['report']['status'] == 'applied-in-isolated-lab-frame', (width, result[theme])
            assert result['machine']['background'] != result['civic']['background'], (width, result)
            assert result['civic']['background'] != result['accessible']['background'], (width, result)
            assert result['machine']['shell'] != result['civic']['shell'], (width, result)
            assert result['before'] == result['after'], (width, result['before'], result['after'])
            assert result['attrBefore'] == result['attrAfter'], (width, result)
            assert result['disabledBefore'] == result['disabledAfter'], (width, result)
            assert not result['fakePresent'], 'A control label cannot create a semantic binding'
            assert result['conflictIgnored'], 'Ambiguous source names cannot be themed'
            assert 'theme-civic' not in result['themeClasses']
            page.close()
        browser.close()
