"""Shared TNG workbench pigment must not fork from the canonical Theme palette.

The tests cover the verified material defects, not unrelated layout regressions.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / 'main_computer/web/applications/styles/mcel-semantic-workbench.css'


def _page(browser, app):
    page = browser.new_page(viewport={'width': 1272, 'height': 768})
    page.set_content(render_semantic_page(app, ROOT, promoted=True))
    return page


def test_one_shared_tng_material_adapter_no_document_color_fork():
    css = CSS.read_text(encoding='utf-8')
    assert '--mcel-workbench-panel-surface:color-mix(in srgb,var(--mcel-theme-card' in css
    assert '--mcel-document-workbench-surface:var(--mcel-workbench-panel-surface)' in css
    assert 'var(--mcel-tng-rail-primary,#ffcb22) 10%' not in css
    assert 'data-mcel-semantic-operational="code-editor"' not in css
    assert 'data-mcel-semantic-operational="task-manager"' not in css
    assert 'background:var(--mcel-workbench-control-surface,#294453)' in css


def test_code_editor_inherits_worker_bronze_with_geometry_unchanged_when_switching_chrome():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = _page(browser, 'code-editor')
            result = page.evaluate('''() => {
              const root = document.getElementById('mcel-workbench');
              const wb = root.querySelector('[data-mcel-document-workbench]');
              const explorer = root.querySelector('[data-mcel-document-explorer]');
              const companion = root.querySelector('[data-mcel-document-companion]');
              const bottom = root.querySelector('[data-mcel-document-bottom]');
              const title = root.querySelector('[data-mcel-document-titlebar]');
              const css = el => getComputedStyle(el).backgroundColor;
              const rect = el => JSON.stringify(el.getBoundingClientRect().toJSON());
              const tng = [wb, explorer, companion, bottom].map(css);
              const before = rect(title);
              root.setAttribute('data-mcel-semantic-chrome', 'chrome-strict-hierarchy');
              const fallback = css(wb), fallbackTitle = rect(title);
              root.setAttribute('data-mcel-semantic-chrome', 'chrome-tng-side-rails');
              return {tng, fallback, before, fallbackTitle, restored:css(wb)};
            }''')
            assert len(set(result['tng'])) == 1, result
            assert result['tng'][0] == 'color(srgb 0.128235 0.116863 0.0639216)', result
            assert result['fallback'] == 'rgb(26, 39, 49)', result
            assert result['before'] == result['fallbackTitle'], result
            assert result['restored'] == result['tng'][0], result
        finally:
            browser.close()


def test_refresh_control_uses_shared_control_paint_but_retains_non_tng_fallback():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            page = _page(browser, 'task-manager')
            result = page.evaluate('''() => {
              const root = document.getElementById('mcel-workbench');
              const btn = root.querySelector('[data-mcel-auto-refresh] [data-mcel-refresh-toggle]');
              const before = getComputedStyle(btn).backgroundColor;
              const text = btn.textContent.trim();
              root.setAttribute('data-mcel-semantic-chrome', 'chrome-strict-hierarchy');
              const fallback = getComputedStyle(btn).backgroundColor;
              root.setAttribute('data-mcel-semantic-chrome', 'chrome-tng-side-rails');
              return {before, fallback, restored:getComputedStyle(btn).backgroundColor,
                sameText:btn.textContent.trim()===text};
            }''')
            assert result['before'] == 'color(srgb 0.137529 0.124235 0.0671765)', result
            assert result['fallback'] == 'rgb(41, 68, 83)', result
            assert result['restored'] == result['before'] and result['sameText'], result
        finally:
            browser.close()


def test_other_generic_workbenches_inherit_the_same_palette_without_document_overrides():
    with sync_playwright() as pw:
        browser = _chrome(pw, None)
        try:
            for app in ('file-manager', 'task-manager'):
                page = _page(browser, app)
                result = page.evaluate('''() => {
                  const root = document.getElementById('mcel-workbench');
                  return {tng:root.getAttribute('data-mcel-semantic-chrome'),
                    panel:getComputedStyle(root).getPropertyValue('--panel').trim(),
                    hasDocument:!!root.querySelector('[data-mcel-document-workbench]')};
                }''')
                assert result['tng'] == 'chrome-tng-side-rails', result
                assert result['panel'] == 'color-mix(in srgb,#090b08 90%,#f6c75b 10%)', (app,result)
                assert not result['hasDocument'], (app,result)
                page.close()
        finally:
            browser.close()
