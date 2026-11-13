"""MCEL Lab themes the actual promoted Calculator child, not retired host markup."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'


def _lab_fixture():
    source = (WEB / 'apps/mcel-lab.html').read_text(encoding='utf-8')
    start = source.index('  <section id="mcel-lab-presentation-workspace"')
    end = source.index('  <section id="mcel-semantic-compatibility"', start)
    return '<div id="mcel-lab-app" data-mcel-workspace-view="presentation">' + source[start:end] + '</div>'


def test_lab_uses_explicit_native_shell_child_and_mounted_ir():
    src = (WEB / 'scripts/mcel-lab.js').read_text(encoding='utf-8')
    assert "'/applications/calculator?mcel-shell-child=1&mcel_lab_theme_preview=1'" in src
    assert "getElementById('mcel-expanded-ir')" in src
    assert "getElementById('mcel-expanded-root')" in src
    assert "source?.application?.appId !== 'semantic-calculator-expanded'" in src
    assert "observeRuntimePresentationTargets({ir: source, root})" in src
    assert "frame.src = '/applications/calculator?mcel_lab_theme_preview=1'" not in src


def test_browser_real_promoted_calculator_preview_theme_switches_and_keeps_semantics():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import render_expanded_page

    child = render_expanded_page(ROOT, promoted=True)
    assert 'id="mcel-expanded-ir"' in child and 'id="mcel-expanded-root"' in child
    html = ('<!doctype html><html><head><style>body{background:#080812;color:white}'
            '.mcel-runtime-preview{display:none}#mcel-lab-app{display:block!important;min-height:100vh}</style></head><body>'
            '<div id="fake-mcel-runtime-preview" class="mcel-runtime-preview theme-machine"></div>'
            + _lab_fixture() + '</body></html>')
    js = (WEB / 'scripts/mcel-lab.js').read_text(encoding='utf-8')
    start = js.index('    // The Calculator experiment never writes to the production Calculator DOM.')
    end = js.index('    // Presentation compatibility inspection is observational only.', start)
    operations = js[start:end]

    with sync_playwright() as p:
        browser = _chrome(p, None)
        for width in (390, 950, 1440):
            page = browser.new_page(viewport={'width': width, 'height': 860})
            page.set_content(html)
            page.add_style_tag(path=str(WEB / 'styles/mcel-lab.css'))
            for script in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js',
                           'mcel-semantic-presentation-bridge.js','mcel-semantic-theme-preview.js',
                           'mcel-diagnostic-plugins.js'):
                page.add_script_tag(path=str(WEB / 'scripts' / script))
            page.add_script_tag(content=(
                '''var mcelLabState={semanticThemePreviewEnabled:false};
                   var mcelThemeSelect=document.getElementById('mcel-theme-select');
                   var mcelRuntimePreview=document.getElementById('fake-mcel-runtime-preview');'''
                + '\n' + operations + '''
                document.getElementById('mcel-semantic-theme-enable').addEventListener('click',enableMcelSemanticThemePreview);
                document.getElementById('mcel-semantic-theme-disable').addEventListener('click',disableMcelSemanticThemePreview);
                document.getElementById('mcel-semantic-theme-frame').addEventListener('load', onMcelSemanticThemeFrameLoad);
                '''))
            page.locator('#mcel-semantic-theme-enable').click()
            page.evaluate('''html => {document.getElementById('mcel-semantic-theme-frame').srcdoc = html;}''', child)
            page.wait_for_function('''() => document.getElementById('mcel-semantic-theme-status')
                                  .textContent.startsWith('Applied ')''', timeout=20000)
            result = page.evaluate('''() => {
              const frame=document.getElementById('mcel-semantic-theme-frame');
              const doc=frame.contentDocument;
              const root=doc.getElementById('mcel-expanded-root');
              const el=root.querySelector('[data-mcel-node][data-node-kind="control"]');
              const region=root.querySelector('[data-mcel-region]');
              const before={id:el.getAttribute('data-mcel-node'), tag:el.tagName,
                disabled:el.querySelector(':disabled')?.disabled ?? null,
                rect:[el.offsetWidth,el.offsetHeight]};
              const colors=[];
              for (const theme of ['theme-machine','theme-civic','theme-debug']) {
                mcelThemeSelect.value=theme;
                mcelRuntimePreview.className='mcel-runtime-preview '+theme;
                const res=applyMcelSemanticThemePreview('live-acceptance');
                const b=el.querySelector('button,[role="tab"]') || el;
                colors.push({theme, status:res.status, control:doc.defaultView.getComputedStyle(b).backgroundColor,
                  region:doc.defaultView.getComputedStyle(region).backgroundColor,
                  controls:res.controls, regions:res.regions});
              }
              return {colors, before,
                after:{id:el.getAttribute('data-mcel-node'), disabled:el.querySelector(':disabled')?.disabled ?? null,
                  rect:[el.offsetWidth,el.offsetHeight]},
                sourceId:JSON.parse(doc.getElementById('mcel-expanded-ir').textContent).application.appId,
                style:!!doc.getElementById('mcel-lab-semantic-theme-color-only'),
                legacyVisible:!document.getElementById('mcel-legacy-site-preview').hidden};
            }''')
            assert result['sourceId']=='semantic-calculator-expanded',result
            assert result['style'] and not result['legacyVisible'],result
            assert result['before']['id'] == result['after']['id'], result
            assert result['before']['disabled'] == result['after']['disabled'], result
            assert result['before']['rect'] == result['after']['rect'], result
            assert all(x['status']=='applied-in-isolated-lab-frame' and x['controls']>=4 and x['regions']>=8 for x in result['colors']), result
            assert len({x['control'] for x in result['colors']}) >= 2, result
            assert len({x['region'] for x in result['colors']}) >= 2, result
            diagnostics = page.evaluate('debug().document.theme')
            assert diagnostics['mountedCalculator']['mountModel'] == 'promoted-operational-runtime', diagnostics
            assert diagnostics['mountedCalculator']['canonicalRootFound'], diagnostics
            assert diagnostics['canonicalSource']['appId'] == 'semantic-calculator-expanded', diagnostics
            assert diagnostics['canonicalSource']['declaredControls'] >= 4, diagnostics
            assert diagnostics['canonicalSource']['declaredRegions'] >= 8, diagnostics
            assert 'calculator-preview-canonical-root-missing' not in diagnostics['issues'], diagnostics
            page.close()
        browser.close()


def test_modern_bridge_rejects_missing_or_ambiguous_canonical_bindings():
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
    from main_computer.mcel_calculator_expanded_host import compile_expanded
    import json

    ir = compile_expanded(ROOT)
    region = ir['surfaces'][0]['interfaceIntent']['regions'][0]['id']
    control = next(n for n in ir['surfaces'][0]['nodes'] if n['nodeKind']=='control')
    with sync_playwright() as p:
        browser = _chrome(p, None)
        page = browser.new_page()
        page.set_content(f'''<div id="root"><section data-mcel-region="{region}">
            <div data-mcel-node="{control['id']}" data-node-kind="control"><button>Mode</button></div>
            </section></div>''')
        page.add_script_tag(path=str(WEB / 'scripts/mcel-semantic-presentation-bridge.js'))
        result = page.evaluate('''ir => {
            const root=document.getElementById('root');
            const original=document.documentElement.outerHTML;
            const report=McelSemanticPresentationBridge.observeRuntimePresentationTargets({ir,root});
            const changed=document.documentElement.outerHTML;
            return {report:{status:report.status, diagnostics:report.diagnostics}, unchanged:original===changed};
        }''', ir)
        assert result['unchanged']
        assert result['report']['status'] == 'unresolved'
        assert any('exact runtime binding' in issue for issue in result['report']['diagnostics'])
        # Duplicates cannot be converted into visual authority.
        result = page.evaluate('''ir => {
            const root=document.getElementById('root');
            const clone=root.querySelector('[data-mcel-node]').cloneNode(true);root.append(clone);
            return McelSemanticPresentationBridge.observeRuntimePresentationTargets({ir,root}).diagnostics;
        }''', ir)
        assert any('duplicated' in issue for issue in result)
        browser.close()
