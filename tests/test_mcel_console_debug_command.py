"""MCEL console debug capture: parent/child reachability and truthful theme diagnostics."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'main_computer/web/applications/scripts/mcel-diagnostic-plugins.js'


def test_debug_command_is_loaded_in_both_application_and_shell():
    html = (ROOT / 'main_computer/web/applications.html').read_text(encoding='utf-8')
    shell = (ROOT / 'main_computer/mcel_applications_shell_host.py').read_text(encoding='utf-8')
    assert '<!-- @include applications/scripts/mcel-diagnostic-plugins.js -->' in html
    assert 'Path("main_computer/web/applications/scripts/mcel-diagnostic-plugins.js")' in shell
    src = SCRIPT.read_text(encoding='utf-8')
    assert 'host.debug = capture' in src
    assert 'host.mcelDebug = capture' in src
    assert 'window.location.search' not in src
    assert 'localStorage' not in src.replace('local storage', '')
    assert 'fetch(' not in src


def test_debug_command_reports_real_lab_boundaries_without_mutation():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        page = browser.new_page()
        page.set_content('''<!doctype html><body>
          <div id="mcel-applications-shell"><iframe id="mcel-shell-frame"></iframe></div>
          <input id="secret" value="PRIVATE-SHOULD-NOT-LEAK">
        </body>''')
        page.add_script_tag(path=str(SCRIPT))
        page.evaluate('''() => {
          const shell = document.getElementById('mcel-shell-frame');
          shell.srcdoc = `<!doctype html><html><body>
            <div id="mcel-lab-app"></div>
            <select id="mcel-theme-select"><option value="theme-debug">Debug</option></select>
            <select id="mcel-chrome-select"><option value="chrome-strict-hierarchy">Strict</option></select>
            <div id="mcel-semantic-theme-status">Waiting for canonical Calculator root to mount.</div>
            <iframe id="mcel-semantic-theme-frame" hidden sandbox="allow-same-origin allow-scripts"
                    srcdoc="<div id='wrong-root'>Not Calculator</div>"></iframe>
            <iframe id="mcel-presentation-preview-frame" sandbox="allow-same-origin" srcdoc="<div>legacy</div>"></iframe>
          </body></html>`;
        }''')
        page.wait_for_function("document.querySelector('#mcel-shell-frame').contentDocument?.querySelector('#mcel-semantic-theme-frame')?.contentDocument?.readyState === 'complete'")
        result = page.evaluate('''() => {
          const before = document.documentElement.outerHTML;
          const output = debug();
          const after = document.documentElement.outerHTML;
          return {output, unchanged: before === after,
            hasAlias: typeof mcelDebug === 'function',
            json: JSON.stringify(output)};
        }''')
        assert result['unchanged']
        assert result['hasAlias']
        assert result['output']['schema'] == 'mcel.console-debug.v1'
        assert result['output']['document']['mcel']['applicationsShell']
        child = result['output']['document']['frames'][0]
        assert child['mcel']['lab']
        assert child['theme']['selectedTheme'] == 'theme-debug'
        assert child['theme']['selectedChrome'] == 'chrome-strict-hierarchy'
        assert child['theme']['frame']['allowsScripts']
        assert child['frames'][1]['frame']['sandboxBlocksScripts']
        assert 'PRIVATE-SHOULD-NOT-LEAK' not in result['json']
        assert 'calculator-canonical-source-missing-or-invalid' not in child['theme']['issues']  # Preview not yet enabled.
        page.evaluate('''() => {
          const child = document.getElementById('mcel-shell-frame').contentWindow;
          child.mcelLabState = {semanticThemePreviewEnabled:true};
          child.document.getElementById('mcel-semantic-theme-frame').hidden = false;
          child.McelLabCanonicalPresentationSources = {calculator:{schema:'mcel.application-ir.v1',application:{appId:'calculator'},surfaces:[{root:'#calculator-app',semanticSurface:{controls:[1,2],regions:[1]}}]}};
        }''')
        result = page.evaluate('debug()')
        child = result['document']['frames'][0]
        assert 'calculator-preview-canonical-root-missing' in child['theme']['issues']
        assert child['theme']['canonicalSource']['declaredControls'] == 2
        assert child['theme']['mountedCalculator']['canonicalRootFound'] is False
        # Inserting the actual root changes only the evidence, not its meaning.
        page.evaluate('''() => {
          const doc = document.getElementById('mcel-shell-frame').contentDocument
             .getElementById('mcel-semantic-theme-frame').contentDocument;
          const root = doc.createElement('div'); root.id='calculator-app';
          root.innerHTML='<button data-mcel-control="intent:calculator.evaluate">Evaluate</button>';
          doc.body.append(root);
        }''')
        result = page.evaluate('debug()')
        child = result['document']['frames'][0]
        assert child['theme']['mountedCalculator']['canonicalRootFound']
        assert child['theme']['mountedCalculator']['canonicalControls'] == 1
        assert 'calculator-theme-style-not-installed' in child['theme']['issues']
        assert 'calculator-preview-canonical-root-missing' not in child['theme']['issues']
        browser.close()


def test_debug_command_excludes_query_strings_and_passwords():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

    with sync_playwright() as playwright:
        browser = _chrome(playwright, None)
        page = browser.new_page()
        page.set_content('<input type="password" value="super-secret"><div id="calculator-app"></div>')
        page.add_script_tag(path=str(SCRIPT))
        packet = page.evaluate('JSON.stringify(debug())')
        assert 'super-secret' not in packet
        assert 'calculatorRoot' in packet
        browser.close()
